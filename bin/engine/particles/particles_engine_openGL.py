import os, time
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QPainter, QFont
from PySide6.QtWidgets import QApplication
from PySide6.QtOpenGLWidgets import QOpenGLWidget
import json
import zipfile

from engine.asset_loader import AssetLoader
from engine.particles.particle_emitter import ParticleEmitter
from engine.data_classes import PetPositionData

from OpenGL.GL import * #type: ignore

from collections import defaultdict

import numpy as np
from numba import njit

from engine.logger import app_logger as log
from engine.logger import debug_logger as debug_log

def get_frame_index(anim, age):
    """
    Given a particle's total frame count and current age, returns the frame_index for the current frame.
    
    :param anim: taken from self.animations dist, contains information about a particle type.
    :param age: age of particle.
    """
    frame_index = int(age * anim["fps"])

    if anim["loop"]:
        frame_index %= anim["frame_count"]
    else:
        frame_index = min(frame_index, anim["frame_count"] - 1)

    return frame_index


#widget drawing particles, fullscreen transparent to clicks
class ParticleOverlayWidget(QOpenGLWidget):
    def __init__(self, pet_position: PetPositionData, RENDER_CONFIG, ASSETS, PARTICLES, archive: zipfile.ZipFile):
        self.ASSETS = ASSETS
        self.PARTICLES = PARTICLES
        self.RENDER_CONFIG = RENDER_CONFIG

        self.pet_position = pet_position

        super().__init__()

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.setWindowFlags(
            Qt.FramelessWindowHint | #type: ignore
            Qt.WindowStaysOnTopHint | #type: ignore
            Qt.Tool #type: ignore
        )

        self.primary_screen = QApplication.primaryScreen()
        avail_geom = self.primary_screen.geometry() # later if needed will use availableGeomtry, but that rquires rewriting rendering code so idk
        self.setGeometry(avail_geom)

        self.clear_surface = True

        # Create dummy data with FINAL types
        dummy_lifetime = np.zeros(len(self.PARTICLES), dtype=np.float32)
        dummy_positions = np.zeros(1000, dtype=np.float32)
        dummy_vels = np.zeros(1000, dtype=np.float32)
        dummy_id = np.zeros(1000, dtype=np.int16)
        dt = np.float32(0.016)

        update_particles(dt, np.uint32(1000), dummy_lifetime, dummy_positions, dummy_positions, dummy_vels, dummy_vels, dummy_vels, dummy_vels, dummy_positions, dummy_id)

        self.window_width = self.width()
        self.window_height = self.height()

        self.taskbar_top = self.primary_screen.availableGeometry().bottom()
        self.taskbar_ndc_y = 1.0 - ((self.taskbar_top / self.primary_screen.geometry().height()) * 2.0)
        print("task y ndc", self.taskbar_ndc_y)

        self.debug_counter = 0 # for debugging

        self.scale = 1

        self.emitters: list[ParticleEmitter] = []
        self.constant_emitters: list[ParticleEmitter] = []

        self.emitters_by_type = defaultdict(int)
        self.particles_by_type = defaultdict(int)

        self.MAX_PARTICLES = self.RENDER_CONFIG.get("max_particle_count", 1000)
        MAX_PARTICLES = self.MAX_PARTICLES

        self.count = np.uint32(0)  # active particle count

        # ---- ARRAYS (SoA) ----
        self.pos_x = np.zeros(MAX_PARTICLES, dtype=np.float32)
        self.pos_y = np.zeros(MAX_PARTICLES, dtype=np.float32)

        self.vel_x = np.zeros(MAX_PARTICLES, dtype=np.float32)
        self.vel_y = np.zeros(MAX_PARTICLES, dtype=np.float32)

        self.acc_x = np.zeros(MAX_PARTICLES, dtype=np.float32)
        self.acc_y = np.zeros(MAX_PARTICLES, dtype=np.float32)

        self.age = np.zeros(MAX_PARTICLES, dtype=np.float32)

        self.size_p = np.zeros(MAX_PARTICLES, dtype=np.float32)

        self.alive = np.zeros(MAX_PARTICLES, dtype=bool)

        self.type_id = np.zeros(MAX_PARTICLES, dtype=np.int16)

        self.anim_lifetimes_by_id = np.zeros(len(self.PARTICLES), dtype=np.float32)

        self.offset_geometry()
        self.show()

        # get all particle animations in a dictionary
        self.animations = []

        #for references
        self.anim_name_to_id = {}

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # we go back three folders
        base = os.path.dirname(project_root)

        self.aspect_ratio_by_id = []


        print("\n----- LOADING PARTICLES -----")
        log.info("---LOADING PARTICLES---")

        for name in list(self.PARTICLES):
            cfg = self.PARTICLES[name]
            asset = f"assets/particles/{cfg['asset']}"
            frames = []

            files = sorted(
                name for name in archive.namelist()
                if name.startswith(asset + "/")
                and name.lower().endswith((".png", ".webp"))
            )
            for filename in files:
                frames.append(filename)

            frame_count = len(list(frames))
            if not frame_count:
                raise RuntimeError(f"No frames found for particle '{name}'")
            
            #registring animations for reference by id
            anim_id = len(self.animations) # starts with 0 and goes up as we add animations
            self.anim_name_to_id[name] = anim_id

            # precompute lifetime
            lifetime = (
                frame_count / cfg["fps"]
                if not cfg["loop"]
                else 1e9)  # effectively infinite
            
            #store lifetime for each particle type by id
            self.anim_lifetimes_by_id[anim_id] = lifetime
            
            self.animations.append({ # we enter them by id to then reference by id
                "name": name,
                "asset":cfg["asset"],
                "frame_count": frame_count,
                "fps": cfg["fps"],
                "loop": cfg["loop"],
                "holds": cfg.get("holds", {}), # holds are not implemented yet
                "times_to_loop": cfg.get("times_to_loop", 1),
            })

            print(f"[PARTICLES LOADED] {name}: {frame_count} frames, asset: {cfg["asset"]}")

        # generating particle texture atlas

        # loading atlas texure
        atlas_path = "generated/atlas/atlas.png"
        config_path = "generated/atlas/atlas.json"

        # getting texture atlas
        print("Getting atlas.png from:", atlas_path)
        log.info(f"Getting atlas.png from: {atlas_path}")

        self.atlas_texture = AssetLoader.load_openGL_texture(archive=archive, path=atlas_path)

        if self.atlas_texture: 
            print("--Success!")
            log.info("--Success!")
        else: 
            log.error(f"  No atlas.png found at '{atlas_path}'")
            raise RuntimeError(f"  No atlas.png found at '{atlas_path}'")

        # getting json config
        print(f"Getting atlas.json from: {atlas_path}")
        log.info(f"Getting atlas.json from: {atlas_path}")

        with archive.open(config_path) as f:
            self.atlas_config = json.load(f)

        if self.atlas_config: 
            print("--Success!")
            log.info("--Success!")
        else:
            log.error(f"  No atlas.json found at '{config_path}'")
            raise RuntimeError(f"No atlas.json found at '{config_path}'")

        # --- loading particle information into memory for fast access ---
        print("---Loading particles into memory---")
        log.info("---Loading particles into memory---")

        self.atlas_lookup = [] # id = full information from the atlas about a particle
        self.frame_lookup = [] # id = which asset lookup to look in

        for asset_name in self.atlas_config["assets"]:
            particle_data = self.atlas_config["assets"][asset_name]

            self.atlas_lookup.append(particle_data)
            self.frame_lookup.append(particle_data["frames"])
            print(f"Asset loaded: {asset_name}")
            log.info(f"Asset loaded: {asset_name}")

        self.asset_ids = {} # assigning each name is ASSETS an id ("dirt": 0, "smoke": 1)
        for i, name in enumerate(self.ASSETS.keys()):
            self.asset_ids[name] = i

        self.asset_lookup = [] # id = which asset to use from the atlas
        self.aspect_ratio_by_id = [] # id = aspect ratio

        for anim in self.animations:
            asset_name = anim["asset"]

            self.aspect_ratio_by_id.append(self.atlas_config["assets"][asset_name]["aspect_ratio"])

            self.asset_lookup.append(
                self.asset_ids[asset_name]
            )

        # print("atlas lookup", self.atlas_lookup)
        print("frame lookup", self.frame_lookup)
        print("assets ids", self.asset_ids)
        print("assets lookup", self.asset_lookup)
        print("aspect_ratio_by_id", self.aspect_ratio_by_id)
        debug_log.info("Debug information:")
        debug_log.info(f"atlas lookup: {self.atlas_lookup}")
        debug_log.info(f"frame lookup: {self.frame_lookup}")
        debug_log.info(f"assets ids: {self.asset_ids}")
        debug_log.info(f"assets lookup: {self.asset_lookup}")
        debug_log.info(f"aspect_ratio_by_id: {self.aspect_ratio_by_id}")

        print("---PARTICLES LOADED---\n")
        log.info("---PARTICLES LOADED---")

    def update_dpi_and_scale(self, new_scale):
        self.scale = new_scale

    def update_hitbox(self, hitbox_width, hitbox_height):
        self.pet_hitbox_w = hitbox_width
        self.pet_hitbox_h = hitbox_height

    def update_taskbar_position(self, taskbar):
        self.taskbar = taskbar

    def clear_constant_emitters(self):
        for emitter in self.constant_emitters:
            emitter.done_emitting = True

        self.constant_emitters.clear()

    def start_emitting(self, name, constant):
        """
        Instanciates an ParticleEmitter for a given name

        Returns the emitter
        
        :param name: name of the particle as stated in config particles.py
        :param constant: whether or not an emitter should have infinite duration (used for constant particles)
        """
        if not self.RENDER_CONFIG.get("render_particles"):
            return
        cfg = self.PARTICLES.get(name)

        if not cfg:
            debug_log.error(f"Particle {name} not found in particles.json")
            raise Exception("Particle", name, "not found in particles.json")
        
        # print("ADDING AN EMITTER", name, constant)

        # making the emitter continuous
        if constant:
            cfg = cfg.copy()
            cfg["duration"] = 1e9
            cfg["total_count"] = 1e9
        
        # print(f"Adding emitter:\n   Name: {name}, \n   cfg: {cfg}")

        new_emitter = ParticleEmitter(particleSystem=self, name=name, cfg=cfg, pet=self.pet_position)

        if constant:
            self.constant_emitters.append(new_emitter)
            # print("particle engine: constant emitters", self.constant_emitters)

        self.emitters.append(new_emitter)

        return new_emitter

    def emit_particle(self, *, pos_x, pos_y, vel_x, vel_y, acc_x, acc_y, name, size):
        if self.count >= self.MAX_PARTICLES:
            return
        
        anim_id = self.anim_name_to_id[name]
        
        i = self.count

        self.pos_x[i] = pos_x
        self.pos_y[i] = pos_y

        self.vel_x[i] = vel_x
        self.vel_y[i] = vel_y

        self.acc_x[i] = acc_x
        self.acc_y[i] = acc_y

        self.age[i] = 0.0
        self.type_id[i] = anim_id
        self.alive[i] = 1

        self.size_p[i] = size

        self.count += 1


    def update_logic(self, dt):
        t0 = time.perf_counter()

        # --- EMITTERS ---
        for emitter in self.emitters:
            emitter.update(dt) # updating all emitters

        self.emitters = [e for e in self.emitters if not e.done_emitting] #pruning emitters

        # --- PARTICLES ---
        if not self.count: return

        self.count = update_particles(
            np.float32(dt),
            self.count,
            self.anim_lifetimes_by_id,
            self.pos_x, self.pos_y,
            self.vel_x, self.vel_y,
            self.acc_x, self.acc_y,
            self.age, self.type_id
            )
        
        # -- DEBUGGING TEXT --
        self.emitters_by_type = defaultdict(int)
        self.particles_by_type = defaultdict(int)

        for emitter in self.emitters:
            self.emitters_by_type[emitter.name] += 1
            self.particles_by_type[emitter.name] += emitter.emitted  # shows only total emitted particles

        # print("( particles \"dirt\"", self.particles_by_type["dirt"], ", time spent", time.perf_counter() - t0, ")") # for debugging
   
    def offset_geometry(self):
        r = self.geometry()
        self.setGeometry(r.x(), r.y(), r.width()+1, r.height())

    # --- DRAWING ---
    def draw(self):
        if not self.count and self.clear_surface: return
        self.update()  # trigger paintGL

    def initializeGL(self):
        # print("initialiseGL")
        glClearColor(0, 0, 0, 0)

        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)

        glEnable(GL_TEXTURE_2D)

        print("Alpha buffer size:", self.context().format().alphaBufferSize())

        print(glGetString(GL_VENDOR))
        print(glGetString(GL_RENDERER))
        print(glGetString(GL_VERSION))

    def resizeGL(self, w, h):
        glViewport(0, 0, w, h)
        self.aspect = w / h
    
    def clear_screen(self):
        self.count = 0
        self.update()

    def paintGL(self):
        t0 = time.perf_counter()
        # self.makeCurrent()

        glClearColor(0, 0, 0, 0)
        glClear(GL_COLOR_BUFFER_BIT)

        if not self.count: 
            self.clear_surface = True
            return
        
        self.clear_surface = False
        # print("count:", self.count)
        
        # дебаг штука
        # total_particles = self.count
        # culled_particles = 0
        # drawn_particles = 0

        vertices = []
        texcoords = []

        for i in range(self.count):
            particle_id = self.type_id[i]
            anim_data = self.animations[particle_id]

            frame_id = int(self.age[i] * anim_data["fps"])
            if anim_data["loop"]:
                frame_id %= anim_data["frame_count"]
            else:
                frame_id = min(frame_id, anim_data["frame_count"] - 1)

            frame_data = self.frame_lookup[self.asset_lookup[particle_id]][frame_id]

            x_px = self.pos_x[i]
            y_px = self.pos_y[i]

            # print(f"part pos {x_px}, {y_px}")

            # conversion from pixels to -1 to 1 (OpenGL coordinates)
            x = (x_px / self.window_width) * 2.0 - 1.0
            y = 1.0 - (y_px / self.window_height) * 2.0

            size = 0.02 * self.size_p[i]
            frame_aspect_ratio = self.aspect_ratio_by_id[particle_id]
 
            sx = size
            sy = size * self.aspect * frame_aspect_ratio

            # skipping if whole quad would be outside of screen
            if (
                x + sx < -1.0 or
                x - sx > 1.0 or
                y < self.taskbar_ndc_y or  # it was:   y + sy < -1.0  before (for the bottom of the screen)
                y - sy > 1.0
            ):
                # culled_particles += 1
                continue

            # drawn_particles += 1

            # creating the quad
            vertices.extend([
                x - sx, y - sy,
                x + sx, y - sy,
                x + sx, y + sy,
                x - sx, y + sy,
            ])

            # getting uv from the atlas
            u0 = frame_data["u0"]
            u1 = frame_data["u1"]

            v0 = frame_data["v0"]
            v1 = frame_data["v1"]

            texcoords.extend([
                u0, v0,
                u1, v0,
                u1, v1,
                u0, v1,
            ])

        # end of loop. now we draw

        t1 = time.perf_counter()

        if not vertices:
            return
        
        vertices = np.array(vertices, dtype=np.float32)
        texcoords = np.array(texcoords, dtype=np.float32)

        glColor4f(1, 1, 1, 1)

        glBindTexture(GL_TEXTURE_2D, self.atlas_texture)

        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_TEXTURE_COORD_ARRAY)

        glVertexPointer(2, GL_FLOAT, 0, vertices)
        glTexCoordPointer(2, GL_FLOAT, 0, texcoords)

        glDrawArrays(GL_QUADS, 0, len(vertices) // 2)

        glDisableClientState(GL_VERTEX_ARRAY)
        glDisableClientState(GL_TEXTURE_COORD_ARRAY)

        glBindTexture(GL_TEXTURE_2D, 0)

        t2 = time.perf_counter()

        # подсчёт партиклов для дебага
        # self.debug_counter += 1

        # if self.debug_counter >= 60:
        #     self.debug_counter = 0

        #     print(
        #         f"Total: {total_particles} | "
        #         f"Drawn: {drawn_particles} | "
        #         f"Culled: {culled_particles}"
        #     )

        # print(f"For i in particle_count: {t1-t0}, Drawing: {t2-t1}")

        # --- DEBUG TEXT ---
        return  # if you want to debug particle coint - dont return
        painter = QPainter(self)
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Consolas", 10))

        lines = []

        for type_name, emitter_count in self.emitters_by_type.items():
            particle_count = self.particles_by_type.get(type_name, 0)

            lines.append(
                f'{emitter_count} emitters of type "{type_name}" – {particle_count} particles'
            )

        lines.append(
            f' \n {(self.count)} active particles, {self.MAX_PARTICLES - self.count} free particles'
        )

        debug_text = "\n".join(lines)

        rect = QRect(10, 20, 300, 200)
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignTop, debug_text) #type: ignore


# Numba method, outside of ParticleEngine class
@njit(cache=True,fastmath=True)
def update_particles(
    dt,
    count,
    max_age_by_id,
    pos_x, pos_y,
    vel_x, vel_y,
    acc_x, acc_y,
    age, type_id
    ):
    i = 0
    while i < count:
        age[i] += dt
        vel_x[i] += acc_x[i] * dt
        vel_y[i] += acc_y[i] * dt
        pos_x[i] += vel_x[i] * dt
        pos_y[i] += vel_y[i] * dt

        # kill conditions
        if age[i] >= max_age_by_id[type_id[i]]: # or pos_y[i] > taskbar_top:  # commented out because it was weird
            last = count - 1
            pos_x[i] = pos_x[last]
            pos_y[i] = pos_y[last]
            vel_x[i] = vel_x[last]
            vel_y[i] = vel_y[last]
            acc_x[i] = acc_x[last]
            acc_y[i] = acc_y[last]
            age[i] = age[last]
            type_id[i] = type_id[last]
            count -= 1
            # print(f"killing a particl cuz {alive[i]} or {pos_y[i]} > {taskbar_top}")
        else:
            i += 1
    return count