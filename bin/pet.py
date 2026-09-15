# Main script with pet behavior: physics, drawing sprites, retrieving data
import time, math, random
from typing import Any
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QPainter, QPen, QPixmap
from PySide6.QtCore import Qt, QTimer, QPointF

import json
import zipfile

from engine.asset_loader import AssetLoader
from engine.state_machine import StateMachine
from engine.click_detector import ClickDetector
from engine.mover import Mover
from engine.animator import Animator
from engine.enums import Flag, Pulse, MovementType, Facing, SurfaceNormal
from engine.vec2 import Vec2
from engine.behaviour_resolver import BehaviourResolver
from engine.windows_detector import WindowsOverlay
from engine.variable_manager import VariableManager
from engine.particles.particles_engine_openGL import ParticleOverlayWidget
from engine.audio_engine import AudioEngine

from engine.data_classes import AnimationData, PetPositionData

from engine.state_commands import *

from engine.logger import app_logger as log
from engine.logger import debug_logger as debug_log

# import cProfile


#region --- HELPERS ---
def scan_animation_bounds(frames: list[QPixmap]) -> tuple[int,int]:
    max_w = 0
    max_h = 0

    for pix in frames:
        max_w = max(max_w, pix.width())
        max_h = max(max_h, pix.height())

    return max_w, max_h

def _convert_string_indexes_to_int(obj: dict, dict_names: list[str]) -> dict:
    """
    Takes in a dictionary object and recursively goes throught it, remapping dictionary keys from str to int ('2' to 2).
    Dict_names is the names of dictionaries.
    """ 
    return _convert_recursive(obj, dict_names)

def _convert_recursive(obj: Any, dict_names: list[str]) -> Any:
    if isinstance(obj, dict):
        result = {}

        for key, value in obj.items():
            if key in dict_names and isinstance(value, dict):
                value = {int(k): v for k, v in value.items()}

            result[key] = _convert_recursive(value, dict_names)

        return result

    if isinstance(obj, list):
        return [_convert_recursive(item, dict_names) for item in obj]

    return obj

#endregion

class Pet(QWidget): # main logic
    def __init__(self, archive: zipfile.ZipFile, main_hwnd):
        super().__init__()
        log.info("---INITIALISATION START---")
        debug_log.info("\n")
        debug_log.info("---CALLING YOJI---")

        config_path = "data/render_config.json"
        with archive.open(config_path) as f:
            try:
                self.RENDER_CONFIG: dict = json.load(f)
                self.LOGIC_FPS = self.RENDER_CONFIG.get("pet_logic_FPS", 30)
                self.PARTICLE_LOGIC_FPS = self.RENDER_CONFIG.get("particles_logic_FPS", 30)
                self.PARTICLE_DRAW_FPS = self.RENDER_CONFIG.get("particles_draw_FPS", 30)
            except json.JSONDecodeError as e:
                msg = f"Invalid JSON syntax in {config_path}\n{e}"
                log.error(msg) 
                raise ValueError(msg)
            except Exception as e:
                msg = f"Could not parse {config_path}\n{e}"
                log.error(msg)
                raise ValueError(msg)

        self.dicts_with_ints_as_keys = ["holds",] # dictionaries with this name will be converted from {"2": 2} to {2: 2}

        self.STATES = self._load_json(archive, "data/states.json", convert_int_keys=True)
        ANIMATIONS = self._load_json(archive, "data/animations.json", convert_int_keys=True)
        VARIABLES = self._load_json(archive, "data/variables.json")
        BEHAVIOURS = self._load_json(archive, "data/behaviours.json")
        ASSETS = self._load_json(archive, "data/particles/assets.json")
        PARTICLES = self._load_json(archive, "data/particles/particles.json")

        ASSETS = json.load(archive.open("data/particles/assets.json"))
        PARTICLES = json.load(archive.open("data/particles/particles.json"))
        SOUNDS = json.load(archive.open("data/sounds.json"))

        log.info("--All .json files loaded: success")

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)   # type: ignore
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        self.current_state = None
        self.previous_state = None
        self.total_time_active = 0

        self._load_animations(animations_json=ANIMATIONS, archive=archive)
    
        self.variable_manager = VariableManager(VARIABLES)

        # self.profiler = cProfile.Profile()
        self.not_first_time_update: bool = False
        # self.start_debugging = False

        self.hitbox_width = 0
        self.hitbox_height = 0

        self.parent_window_hwnd: int|None = None
        self.parent_window_rect_last = None

        self.stay_on_window_when_resize = self.RENDER_CONFIG.get("stay_on_window_when_resize", False) 

        self.mover = Mover(self)
        self.mover.reset_settings(self.RENDER_CONFIG) # needs to be done immediately to apply settings

        self.primary_screen = QApplication.primaryScreen()
        self.taskbar_top = self.primary_screen.availableGeometry().bottom() # Taskbar position detection
        init_pos = Vec2(self.RENDER_CONFIG.get("initial_position", (100, 0)))
        self.mover.set_position(init_pos.x, self.taskbar_top + init_pos.y + 1) # set initial position

        init_pos = Vec2(init_pos.x, self.taskbar_top + init_pos.y + 1)
        # self.anchor = init_pos

        self.position = PetPositionData(
            center               = init_pos,
            parent_surface_type  = None,
            hitbox_height        = 10,
            hitbox_width         = 10
        )

        cfg_facing = self.RENDER_CONFIG.get("default_facing")
        self.facing: Facing = Facing.__members__.get(cfg_facing, Facing.RIGHT) # type: ignore  # defining facing direction

        self.behaviour_resolver = BehaviourResolver(self, self.position, BEHAVIOURS)

        self.windowsOverlay = WindowsOverlay(self)

        self.particle_engine = ParticleOverlayWidget(pet_position=self.position, RENDER_CONFIG=self.RENDER_CONFIG ,ASSETS=ASSETS, PARTICLES=PARTICLES, archive=archive)
        self.particle_logic_acc = 0
        self.particle_draw_acc = 0

        self.audio_engine = AudioEngine(sounds=SOUNDS, archive=archive)
        
        initial_state = self.RENDER_CONFIG.get("default_state", next(iter(self.STATES))) #either get the "default_state" from the RENDER_CONFIG, or the first item in the self.STATES dictinary
        
        self.state_machine = StateMachine(pet=self, CONFIG=self.STATES, initial=initial_state, variable_manager=self.variable_manager) # set initial state
        self.click_detector = ClickDetector(pet=self, state_machine=self.state_machine)

        self.animator = Animator(pet=self, state_machine=self.state_machine)
        
        self.on_state_enter(initial_state)

        h = self.primary_screen.availableGeometry().height()
        self.update_dpi_and_scale(h=h, initial_state=initial_state)

        max_measurement = max(self.max_bounds_w, self.max_bounds_h)
        self.resize_keep_anchor(int(max_measurement * self.scale * 2), int(max_measurement * self.scale * 2))

        self.last_mouse_pos = Vec2()

        self.drag_offset = Vec2(0,0)
        self.rotation_angle = 0
        
        anim_name = self.RENDER_CONFIG.get("hitbox_from_animation")
        if anim_name not in self.animations:
            cfg = self.STATES[initial_state]      # gets the config for the state from states.py
            anim_name = self._resolve_animation(cfg.get("animation", []))
        frame = self.animations[anim_name].frames[0]
        self.update_hitbox_size_and_drag_offset(frame=frame) # initial hitbox update

        self.prev_frame_index: int = -1

        print("---LOADING SUCCESSFUL---\n")
        log.info("---LOADING SUCCESSFUL---\nEnjoy your yoji <3\n")
        debug_log.info("---Yoji loaded---\n")

        self._start_qtimer()
        

    def _start_qtimer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_logic) 
        self.timer.start(1000 // self.LOGIC_FPS)

    def _load_animations(self, animations_json, archive: zipfile.ZipFile):
        log.info("---LOADING ANIMATIONS---")
        print("--- LOADING ANIMATIONS ---")
        self.animations: dict[str, AnimationData] = {}
        max_bounds_w = 0
        max_bounds_h = 0
        default_loop_option: bool = self.RENDER_CONFIG.get("default_loop_option", False)

        for animation_name in list(animations_json):
            cfg: dict = animations_json[animation_name]
            folder = cfg.get("folder")

            folder = f"assets/animations/{folder}"

            folder_exists = zipfile.Path(archive, folder+"/").exists()
            if not folder_exists: raise RuntimeError(f"No folder provided for animation \"{animation_name}\".\nMake sure folder assets/animations/{folder} exists.")
            
            frames = AssetLoader.load_QPixmap_frames(archive=archive, folder=folder)
            if not frames: raise RuntimeError(f"No frames found for animation '{animation_name}' in folder {folder}")
            
            bounds_w, bounds_h = scan_animation_bounds(frames)
            self.max_bounds_w = max(max_bounds_w, bounds_w)
            self.max_bounds_h = max(max_bounds_h, bounds_h)


            # variants: list[AnimationVariant] = []
            # var_cfg = cfg.get("variants")
            # if var_cfg:
            #     probability_sum = sum(
            #         probability
            #         for _, probability
            #         in cfg["variants"]
            #     )
            #     if not math.isclose(probability_sum, 1, abs_tol=0.001):
            #         raise ValueError(f"[ANIMATION]{animation_name} variants probabilities must sum to 1.0")
                
            #     for folder, probability in (cfg["variants"]):
            #         path = (f"assets/animations/{folder}")
            #         var_frames = AssetLoader.load_QPixmap_frames(archive=archive, folder=path)

            #         variant = AnimationVariant(
            #             frames=var_frames,
            #             weight=probability
            #         )
                
            #         variants.append(variant)

            #         print(f"[ANIMATION] {animation_name} variants: {folder, probability}: {len(var_frames)} frames")
            #         log.debug(f"[ANIMATION] {animation_name} variants: {folder, probability}: {len(var_frames)} frames")

            anim_data = AnimationData(
                frames=frames,
                fps=cfg.get("fps", 12),
                holds=cfg.get("holds", {}),
                loop=cfg.get("loop", default_loop_option),
                times_to_loop=cfg.get("times_to_loop", 1),
                bounds=(bounds_w, bounds_h),
            )

            self.animations[animation_name] = anim_data

            print(f"[ANIMATION LOADED] {animation_name}: {len(frames)} frames")
            log.info(f"[ANIMATION LOADED] {animation_name}: {len(frames)} frames")                

    def _load_json(self, archive: zipfile.ZipFile, path, convert_int_keys=False):
        with archive.open(path) as f:
            try:
                data = json.load(f)

                if convert_int_keys:
                    data = _convert_string_indexes_to_int(
                        data,
                        self.dicts_with_ints_as_keys)

                log.info(f"{path} loaded: success")
                return data
            
            except json.JSONDecodeError as e:
                msg = f"Invalid JSON syntax in {path}\n{e}"
                log.error(msg)
                raise ValueError(msg)
            except Exception as e:
                msg = f"Could not parse {path}\n{e}"
                log.error(msg)
                raise ValueError(msg)


    def on_state_enter(self, state): # called in state_machine when entering a new state
        print("STATE:", state)
        self.current_state = state
        # if self.parent_window_hwnd:
        #     # print(f"Position: {self.position.anchor.x}, {self.position.anchor.y}\nState: {self.current_state}\nParent window: {self.parent_window_hwnd}\nParent window position: {self.parent_window_rect_last}")

        cfg: dict = self.STATES[state]

        next_behaviour = cfg.get("behaviour", "STATIONARY")
        self._resolve_behavior(next_behaviour, cfg)

        if self.mover.movement_type == MovementType.DRAG and self.parent_window_hwnd:
            self._clear_parent_window()

        # isAbletoRotate = True if self.mover.movement_type == MovementType.DRAG else False   # not used anymore but maybe later
        next_anim = self._resolve_animation(cfg.get("animation", []))

        print("next anim", next_anim)
        
        debug_log.info(f"--->")
        debug_log.info(f"Entering state {state}, behaviour: {next_behaviour}, animation: {next_anim}")

        self.play_animation(anim_name=next_anim, cfg=cfg)

    def _process_commands(self, commands: list):
        # print("Processing commands", commands)
        for cmd in commands:
            match cmd:
                case VariableCommand(name=name, op=op, value=value):
                    if op == "+=":
                        self.variable_manager.add_var(name, value) 
                    elif op == "-=": 
                        self.variable_manager.add_var(name, value, substract=True)
                    elif op == "=":
                        self.variable_manager.set_var(name, value)

                case BoolCommand(name=name, value=value):
                    self.variable_manager.set_bool(name, value)

                case ParticleCommand(name=name, constant=constant):
                    self.particle_engine.raise_()
                    self.particle_engine.start_emitting(name, constant)

                case AudioCommand(name=name, volume=volume, speed=speed):
                    self.audio_engine.play(name, volume=volume, speed=speed )
        
    def on_state_exit(self, state): # triggered twice if transition animation exists
        if state == self.previous_state:
            debug_log.info(f"Transition animation ended ->")
            return
        self.previous_state = state
        self.particle_engine.clear_constant_emitters()
        
        debug_log.info(f"Exiting state {state}")
        

    def _resolve_behavior(self, behaviour, cfg):
        # print(self.behaviour_name)
        self.behaviour_name = behaviour
        target_x, target_y, type, mover_settings, collision_settings, parenting_settings = self.behaviour_resolver.resolve(self.behaviour_name)
        self.surface_to_collide_with = collision_settings
        self.surfaces_to_parent_to = parenting_settings

        if mover_settings: # using mover settings from behaviours first
            acceleration = mover_settings.get("acceleration", self.mover.acceleration)
            max_speed = mover_settings.get("max_speed", self.mover.max_speed)
            slow_radius = mover_settings.get("slow_radius", self.mover.slow_radius)
            snap_distance = mover_settings.get("snap_distance", self.mover.snap_distance)
            # drag specific
            max_angle = mover_settings.get("max_angle", self.mover.max_angle)
            inertia = mover_settings.get("inertia", self.mover.inertia)
            damping = mover_settings.get("damping", self.mover.damping)
            # jump specific
            jump_velocity = mover_settings.get("jump_velocity", self.mover.jump_velocity)
            gravity = mover_settings.get("gravity", self.mover.gravity)
            self.mover.set_settings(acceleration=acceleration, max_speed=max_speed, slow_radius=slow_radius, snap_distance=snap_distance, max_angle=max_angle, inertia=inertia, damping=damping, jump_velocity=jump_velocity,gravity=gravity)

        movement_settings = cfg.get("settings", {}) # get mover settings from states.py

        if movement_settings: # adding overrides from states
            acceleration = movement_settings.get("acceleration", self.mover.acceleration)
            max_speed = movement_settings.get("max_speed", self.mover.max_speed)
            slow_radius = movement_settings.get("slow_radius", self.mover.slow_radius)
            snap_distance = movement_settings.get("snap_distance", self.mover.snap_distance)
            # drag specific
            max_angle = movement_settings.get("max_angle", self.mover.max_angle)
            inertia = movement_settings.get("inertia", self.mover.inertia)
            damping = movement_settings.get("damping", self.mover.damping)
            # jump specific
            jump_velocity = movement_settings.get("jump_velocity", self.mover.jump_velocity)
            gravity = movement_settings.get("gravity", self.mover.gravity)
            self.mover.set_settings(acceleration=acceleration, max_speed=max_speed, slow_radius=slow_radius, snap_distance=snap_distance, max_angle=max_angle, inertia=inertia, damping=damping, jump_velocity=jump_velocity,gravity=gravity)
        else:
            self.mover.reset_settings(self.RENDER_CONFIG)

        if type == MovementType.STATIONARY: # hardcoded doing nothing for stationary
            return

        if type == MovementType.DRAG:  # hardcoded behaviour for drag
            self.mover.movement_type = MovementType.DRAG

            if not self.click_detector.press_pos: #safe check
                self.mover.end_drag()
                return
            
            pos = Vec2(self.click_detector.press_pos.x(), self.click_detector.press_pos.y())
            self.mover.begin_drag(pos)
            return

        # print("on state change", end="")
        self.mover.move_to(target_x, target_y, type)

    def _resolve_animation(self, animation_cfg: str | list[list]) -> str:
        if isinstance(animation_cfg, str):
            return animation_cfg

        probability_sum = sum(
                probability
                for _, probability
                in animation_cfg
            )
        if not math.isclose(probability_sum, 1, abs_tol=0.001):
            raise ValueError(f"[ANIMATION]{self.current_state} animation variants probabilities must sum to 1.0")
        
        r = random.random()
        acc = 0.0
        for animation, probability in animation_cfg:
            acc += probability
            if r <= acc:
                return animation
            
        return animation_cfg[-1][0]

    def play_animation(self, anim_name: str, cfg: dict, isTransitionAnimation = False):
        if anim_name not in self.animations:
            debug_log.error(f"{__name__}: Animation {anim_name} not found in animations.json")
            raise Exception(f"Animation {anim_name} not found in animations.json")

        animations_cfg = self.animations[anim_name]

        frames = self.animations[anim_name].frames
        fps: float = cfg.get("fps", animations_cfg.fps)
        loop: bool = cfg.get("loop", animations_cfg.loop)
        times_to_loop: int = cfg.get("times_to_loop", animations_cfg.times_to_loop)
        holds: dict = cfg.get("holds", animations_cfg.holds)

        # bounds_w, bounds_h = self.animations[anim_name]["bounds"]  # not used yet but its there if needed

        if isTransitionAnimation:
            loop = False
            # print("transition animation playing")

        transit_txt = "transition " if isTransitionAnimation else ""
        debug_log.debug(f"Playing {transit_txt}animation: {anim_name}, Frame count: {len(frames)}, Loop: {loop}, Times to loop: {times_to_loop}, Holds: {holds}")

        # print("Starting animation:", anim_name, " Frame count:", len(frames), " Loop:", loop, " Times to loop:", times_to_loop, " Holds:", holds)
        self.animator.set_animation(frames=frames, fps=fps, holds=holds, loop=loop, times_to_loop=times_to_loop)


    def update_logic(self):  # UPDATE LOGIC
        dt = 1 / self.LOGIC_FPS

        self.total_time_active += dt

        # if self.start_debugging:
        #     self.profiler.disable()
        #     self.profiler.enable()  # start profiling
        
        if self.mover.movement_type == MovementType.DRAG:
            self.mover.update_drag_target(self.last_mouse_pos, dt)
    
        self.click_detector.update()
        self.variable_manager.update(dt)

        t1 = time.perf_counter()

        # --- getting the parent window rect
        self.parent_window_rect = None
        followed_parent = False

        if self.parent_window_hwnd:
            rect = self.windowsOverlay.update_parent_window(self.parent_window_hwnd)

            if rect: self.parent_window_rect = rect
            else: self._clear_parent_window()

            # print(self.parent_window_hwnd)

            followed_parent = self._follow_parent_window(self.parent_window_rect)
            # print("followed_parent:", followed_parent)

            if not followed_parent:
                # print("checking visible seg")
                on_visible_segment = self.windowsOverlay.check_parent_window_segment(self.position, self.parent_window_hwnd, self.parent_surface_type)
                # print(on_visible_segment)
                if not on_visible_segment:
                    self._clear_parent_window()
                    # print(f"cleared window because was not on visible segment\n{self.position.anchor.x, self.position.anchor.y}\nfollowed={followed_parent}, {self.parent_window_rect}")

        t3 = time.perf_counter()

        # --- updating Mover and collisions ---
        arrived = self.mover.update(dt)
        
        dx = self.mover.pos.x - self.position.anchor.x
        dy = self.mover.pos.y - self.position.anchor.y

        col_x, col_y = False, False
        surface_data = None

        # --- checking for collisions and applying delta ---
        if self.mover.movement_type != MovementType.DRAG and dx != 0:
            # print("arrived", arrived)
            dx, col_x, surface_data = self.windowsOverlay.collide_horizontal(self.position, dx, collision_mask=self.surface_to_collide_with)

        self.position.anchor.x += dx

        if not col_x and self.mover.movement_type != MovementType.DRAG and dy != 0:
            dy, col_y, surface_data = self.windowsOverlay.collide_vertical(self.position, dy, collision_mask=self.surface_to_collide_with)
            # print(dy)
        
        self.position.anchor.y += dy
        
        # --- if mover reached destination or collision occured - movement finished
        if arrived or col_x or col_y:
            # print("col_x: ", col_x, "self.surfaces: ", self.surfaces_to_parent_to)
            # print("making mover set position cuz", arrived, col_x, col_y)
            # print("if arrived", end="")
            self.mover.set_position(self.position.anchor.x, self.position.anchor.y)
            self.click_detector.release()
            self.state_machine.raise_flag(Flag.MOVEMENT_FINISHED)

            if surface_data:
                # if not col_y: col_y = False
                # print("checking parenting", col_y, "in", self.surfaces_to_parent_to)
                # print("checking parenting is ", col_y in self.surfaces_to_parent_to)
                col_surface = col_x if col_x else col_y
                self._set_parent_window(col_surface, surface_data)

            # arrived, col_x, col_y = False, False, False

        # print("position is", self.mover.pos.x, self.mover.pos.y)
        # print("facing is", self.facing)
        t5 = time.perf_counter()

        transition_data, commands = self.state_machine.update(dt)
        if transition_data:
            self.on_state_exit(self.current_state)

            next_state, transition_anim, transition_anim_cfg = transition_data
            if transition_anim:
                self.play_animation(transition_anim, transition_anim_cfg, isTransitionAnimation=True)
            else:
                self.on_state_enter(next_state)

        self._process_commands(commands)
        
        t6 = time.perf_counter()

        # --- SYNC PHASE ---
        self._clamp_position_to_screen()

        if dx or dy or followed_parent:
            self.apply_window_position()
        t7 = time.perf_counter()

        # checking if next frame is not the same as current and updating then
        self.animator.update(dt)
        t4 = time.perf_counter()

        index = self.animator.index

        if not self.prev_frame_index: self.prev_frame_index = index -  1 # kinda useless but lets keep it for now

        if index != self.prev_frame_index or self.mover.movement_type == MovementType.DRAG: 
            self.update()  # repaint
        self.prev_frame_index = index

        # --- UPDATING PARTICLES ---
        self.particle_logic_acc += dt
        self.particle_draw_acc += dt
        t8 = time.perf_counter()

        if self.particle_logic_acc >= 1 / self.PARTICLE_LOGIC_FPS:
            self.particle_logic_acc -= 1 / self.PARTICLE_LOGIC_FPS
            self.particle_engine.update_logic(1 / self.PARTICLE_LOGIC_FPS)

        t9 = time.perf_counter()

        if self.particle_draw_acc >= 1 / self.PARTICLE_DRAW_FPS:
            self.particle_draw_acc -= 1 / self.PARTICLE_DRAW_FPS
            self.particle_engine.draw()

        t10 = time.perf_counter()
        # print(f"Particles: Update: {t9-t8}    Draw: {t10-t9}")

        # print(f"update windows frames takes {t3-t1}")
        # self.profiler.disable()  # stop profiling
        # self.profiler.dump_stats("test.prof")


    def update_apps(self, app_state):
        self.state_machine.update_apps(app_state)

    def _clamp_position_to_screen(self):
        clamped_x = min(self.primary_screen.availableGeometry().width() - self.hitbox_width / 2, max(self.position.anchor.x, self.hitbox_width / 2))
        clamped_y = min(self.primary_screen.geometry().bottom(), max(self.position.anchor.y, self.hitbox_height))

        if self.position.anchor.y < self.hitbox_height:
            self._clear_parent_window()

        dx = clamped_x - self.position.anchor.x
        dy = clamped_y - self.position.anchor.y

        self.mover.move_global(dx,dy)

        self.position.anchor.x = clamped_x
        self.position.anchor.y = clamped_y

    def _follow_parent_window(self, rect: tuple|None) -> bool:
        if not self.parent_window_hwnd:
            return False

        if not rect or not self.parent_window_rect_last:
            self.parent_window_rect_last = rect
            return False
        
        followed = False
        anchor_x = self.position.anchor.x
        anchor_y = self.position.anchor.y

        x1, y1, x2, y2 = rect
        px1, py1, px2, py2 = self.parent_window_rect_last
        dx, dy = 0, 0

        # following general movement
        global_move_x = (x1 - px1) == (x2 - px2)
        global_move_y = (y1 - py1) == (y2 - py2)

        match self.parent_surface_type:
            case SurfaceNormal.LEFT:
                if global_move_y: dy = y1 - py1 
                dx = x1 - px1
                if anchor_x != x1: dx = x1 - anchor_x
            case SurfaceNormal.UP:
                if global_move_x: dx = x1 - px1 
                dy = y1 - py1
                if anchor_y != y1: dy = y1 - anchor_y
            case SurfaceNormal.RIGHT:
                if global_move_y: dy = y1 - py1 
                dx = x2 - px2
                if anchor_x != x2: dx = x2 - anchor_x
            case SurfaceNormal.DOWN:
                if global_move_x: dx = x1 - px1 
                dy = y2 - py2
                if anchor_y != y2: dy = y2 - anchor_y

        # staying on windows or falling off
        resize: bool = False
        resize_move_x: int
        resize_move_y: int

        if self.stay_on_window_when_resize:
            if self.parent_surface_type == SurfaceNormal.UP or self.parent_surface_type == SurfaceNormal.DOWN:
                if anchor_x < x1 + self.hitbox_width/2:
                    anchor_x = x1 + self.hitbox_width/2
                    resize = True
                elif anchor_x > x2 - self.hitbox_width/2:
                    anchor_x = x2 - self.hitbox_width/2
                    resize = True
            elif self.parent_surface_type == SurfaceNormal.LEFT or self.parent_surface_type == SurfaceNormal.RIGHT:
                if anchor_y < y1 + self.hitbox_height:
                    anchor_y = y1 + self.hitbox_height
                    resize = True
                elif anchor_y > y2:
                    anchor_y = y2
                    resize = True
            
            if resize: 
                self.mover.set_position(anchor_x, anchor_y)  # moving to the edge when resizing
                self.anchor = Vec2(anchor_x, anchor_y)
                # self.position.anchor = Vec2(anchor_x, anchor_y)

        # if self.RENDER_CONFIG "stay_on_window_when_resize" == False pet should just fall off
        else:
            if not global_move_x and self.parent_surface_type in (SurfaceNormal.UP, SurfaceNormal.DOWN): # its much nicer to read, i hope its not too bad for performance
                if anchor_x <= x1 - 2 or anchor_x >= x2 + 2:
                    self._clear_parent_window()
            if not global_move_y and self.parent_surface_type in (SurfaceNormal.LEFT, SurfaceNormal.RIGHT):
                if anchor_y <= y1 - 2 or anchor_y >= y2 + 2:
                    self._clear_parent_window()

        self.parent_window_rect_last = rect

        # applying global movement
        if dx != 0 or dy != 0:
            self.mover.move_global(dx, dy)
            followed = True

        return followed

    def _clear_parent_window(self):
        self.state_machine.pulse(Pulse.LOST_PARENT)
        self.state_machine.raise_flag(Flag.NOT_PARENTED_TO_WINDOW)
        self.state_machine.remove_flag(Flag.PARENTED_TO_WINDOW)
        self.parent_window_hwnd = None
        self.parent_surface_type = None
        self.position.parent_surface_type = None
        self.parent_window_rect_last = None

    def _set_parent_window(self, collision_surface, surface_data):
        hwnd = surface_data[0]

        if hwnd == self.windowsOverlay.TASKBAR_HWND: return
 
        self.parent_surface_type = collision_surface
        self.position.parent_surface_type = collision_surface

        # print("surface type:", self.parent_surface_type)

        self.parent_window_rect_last = self.windowsOverlay.update_parent_window(hwnd)

        self.parent_window_hwnd = hwnd
        self.state_machine.pulse(Pulse.GAINED_PARENT)
        self.state_machine.raise_flag(Flag.PARENTED_TO_WINDOW)
        self.state_machine.remove_flag(Flag.NOT_PARENTED_TO_WINDOW)
        # print("Parent window:", hwnd)

    def apply_window_position(self):
        self.move(
            int(self.position.anchor.x - self.width() / 2),
            int(self.position.anchor.y - self.height())
        )

    def resize_keep_anchor(self, new_w, new_h):
        new_x = self.position.anchor.x - new_w // 2
        new_y = self.position.anchor.y - new_h
        self.setGeometry(new_x, new_y, new_w, new_h)
    
    def update_dpi_and_scale(self, h, initial_state):
        percentage = self.RENDER_CONFIG["pet_size_on_screen"] / 100
        
        self.dpi_scale = self.devicePixelRatioF()
        first_animation_cfg = self.STATES[initial_state]["animation"]
        first_animation = self._resolve_animation(first_animation_cfg)
        first_frame = self.animations[first_animation].frames[0]
        self.pixel_ratio = (h * percentage) / first_frame.height() / self.dpi_scale
        print("screen height", h)
        print("first frame h:", first_frame.height())
        print("pixel ratio", self.pixel_ratio)

        self.scale = self.pixel_ratio * self.dpi_scale

        self.particle_engine.update_dpi_and_scale(self.scale)
        self.particle_engine.update_taskbar_position(self.taskbar_top)

        print("screen dpi", self.dpi_scale)
        print("new scale", self.scale)

        log.info(f"Updating dpi and scale:\nScreen height: {h}\n First frame height: {first_frame.height()}\nPixel ratio: {self.pixel_ratio}\nScreen DPI: {self.dpi_scale}\nNew scale: {self.scale}")

    def update_hitbox_size_and_drag_offset(self, frame):
            if not frame:
                frame = self.animator.get_frame()
                      
            self.hitbox_width = frame.width() * self.scale
            self.hitbox_height = frame.height() * self.scale

            self.windowsOverlay.update_hitbox(self.hitbox_width, self.hitbox_height)
            self.particle_engine.update_hitbox(self.hitbox_width, self.hitbox_height)

            self.position.set_hitbox(self.hitbox_width, self.hitbox_height)

            # print(self.hitbox_height)
            # print(self.hitbox_width)

            self.drag_offset = Vec2(self.hitbox_width * self.RENDER_CONFIG["drag_offset_x"], self.hitbox_height * self.RENDER_CONFIG["drag_offset_y"])
            self.mover.drag_offset = self.drag_offset

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton: # type: ignore
            p = event.globalPosition()
            self.click_detector.press(p)
            self.last_mouse_pos =  Vec2(p.x(), p.y())

    def mouseMoveEvent(self, event):
        p = event.globalPosition()
        self.click_detector.move(p)
        self.last_mouse_pos =  Vec2(p.x(), p.y())

    def mouseReleaseEvent(self, event):
        self.click_detector.release()
        self.variable_manager.add_var("times_clicked_this_state", 1)
        if self.mover.movement_type == MovementType.DRAG:
            self.mover.end_drag()      

    def focusOutEvent(self, event):
        self.mover.end_drag()  

    def leaveEvent(self, event):
        self.mover.end_drag()  

    def keyPressEvent(self, e): #doesnt work when app is in background
        if e.key() == Qt.Key.Key_F4:
            print(self.position)
            # print(f"______________________________\n\n  PET REPORT\n\nPosition: {self.position.anchor.x}, {self.position.anchor.y}\nState: {self.current_state}\nCurrent behaviour: {self.behaviour_name}\nParent window: {self.parent_window_hwnd}\nParent window position: {self.parent_window_rect_last}\n\n  ^^^.>.\n______________________________")
    #     elif e.key() == Qt.Key.Key_L:
    #         print("Start debugging")
    #         self.start_debugging = True

    def paintEvent(self, e): #draws the frame reveived from Animator 
        frame = self.animator.get_frame()
        if not frame:
            return
        
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True) # pyright: ignore[reportAttributeAccessIssue]

        # p.fillRect(self.rect(), QColor(80, 80, 80))  # dark gray

        # draw sprite so its bottom-middle is at (self.x, self.y)
        anchor_x = self.width() // 2
        anchor_y = self.height()

        offset_x: int = frame.width() // 2
        offset_y: int = frame.height()

        p.save()

        sx = self.scale
        if self.facing == Facing.LEFT:
            sx *= -1

        p.translate(anchor_x, anchor_y)

        # draws pets hitbox, pretty neat (says there are problems but works anyway)
        # p.setPen(QPen(Qt.GlobalColor.red, 3))
        # p.drawRect(int(self.position.left), int(self.position.top), int(self.position.hitbox_width), int(self.position.hitbox_height))

        # p.setPen(QPen(Qt.GlobalColor.green, 6))
        # p.drawEllipse(QPointF(0, 0), 2, 2)

        # p.setPen(QPen(Qt.GlobalColor.blue, 6))
        # p.drawEllipse(QPointF(0, 0), 2, 2)

        # p.setPen(QPen(Qt.GlobalColor.blue, 3))
        # p.drawLine(self.width(), 0, 0, self.height())
        # p.drawLine(offset_x, offset_y, anchor_x, anchor_y)

        if self.rotation_angle != 0:
            cx, cy = self.drag_offset
            p.translate(cx, cy)
            p.rotate(self.rotation_angle)
            p.translate(-cx, -cy)

        p.scale(sx, self.scale)
        p.drawPixmap(-offset_x, -offset_y, frame)

        p.restore()

    def recall(self):
        self.particle_engine.clear_screen()
        debug_log.info(f"Pet is being recalled. Has been active for {self.total_time_active/60:.2f} minutes")
        debug_log.info(f"Goodbye!")