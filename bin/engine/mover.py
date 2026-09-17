from engine.enums import Flag, Pulse, MovementType, Facing
from engine.vec2 import Vec2
import math

class Mover:
    def __init__(self, pet):
        self.pos = Vec2()
        self.vel = Vec2()
        self.target = Vec2()

        self.pet = pet

        self.movement_type = None
        self.active = False

        self.drag_offset = Vec2()

        # drag specific
        self.angle = 0
        self.angular_vel = 0

        # jump specific
        self.grounded_y = None

    def reset_settings(self, RENDER_CONFIG):
        self.acceleration = RENDER_CONFIG.get("acceleration", 1200)
        self.max_speed = RENDER_CONFIG.get("max_speed", 700)
        self.slow_radius= RENDER_CONFIG.get("slow_radius", 120)
        self.snap_distance= RENDER_CONFIG.get("snap_distance", 5)

        self.movement_type = None
        self.active = False

        # drag specific
        self.max_angle = RENDER_CONFIG.get("max_angle", 90)
        self.inertia = RENDER_CONFIG.get("inertia", 1)
        self.damping = RENDER_CONFIG.get("damping", 1)
        self.gravity = RENDER_CONFIG.get("gravity", 4000)

        # jump specific
        self.jump_velocity = RENDER_CONFIG.get("jump_velocity", 1000)
        self.gravity = RENDER_CONFIG.get("gravity", 2500)

    def set_settings(self, acceleration, max_speed, slow_radius, snap_distance, max_angle, inertia, damping, jump_velocity, gravity):
        self.acceleration = acceleration
        self.max_speed = max_speed
        self.slow_radius = slow_radius
        self.snap_distance = snap_distance
        # drag specific
        self.max_angle = max_angle
        self.inertia = inertia
        self.damping = damping
        # jump specific
        self.jump_velocity = jump_velocity
        self.gravity = gravity

    def set_position(self, x: float | Vec2 = 0.0, y: float | None =None):
        if y is None and isinstance(x, Vec2):
            self.pos = x
        elif  isinstance(x, float):
            self.pos = Vec2(x, y)
        self.vel = Vec2()
        # print("Mover set position at", self.pos.x, self.pos.y)
        self.active = False

    def move_global(self, dx, dy):
        self.pos.x += dx
        self.pos.y += dy
        self.target.x += dx
        self.target.y += dy

    def move_to(self, x, y, movement_type: MovementType):
        self.active = True
        if self.vel == None: return
        self.target = Vec2(x, y)
        self.movement_type = movement_type

        if x < self.pos.x:
            self.pet.facing = Facing.LEFT
        elif x > self.pos.x:
            self.pet.facing = Facing.RIGHT

        if movement_type == MovementType.INSTANT:
            self.set_position(x, y)
            self.pet._clear_parent_window()
        
        if movement_type == MovementType.JUMP:
            self.grounded_y = self.pos.y
            self.pos.y -= 1 
            self.vel.y = -self.jump_velocity
            self.pet._clear_parent_window()

        # print(pet.facing)

    def update(self, dt):
        if not self.active:
            return False

        match self.movement_type:

            case MovementType.DRAG:
                return False

            case MovementType.INSTANT:
                return True

            case MovementType.LINEAR:
                return self._update_linear(dt)

            case MovementType.ACCELERATION:
                return self._update_acceleration(dt)

            case MovementType.LERP:
                return self._update_lerp(dt)

            case MovementType.JUMP:
                return self._update_jump(dt)

    # ---------------- movement types ---------------- #

    def _update_linear(self, dt):
        direction = (self.target - self.pos).normalized()
        self.vel = direction * self.max_speed
        self.pos += self.vel * dt

        if self.pos.distance_to(self.target) <= self.snap_distance:
            self.pos = self.target.copy()
            self.active = False
            return True

        return False

    def _update_acceleration(self, dt):
        direction = (self.target - self.pos).normalized()
        self.vel += direction * self.acceleration * dt

        if self.vel.length() > self.max_speed:
            self.vel = self.vel.normalized() * self.max_speed

        self.pos += self.vel * dt

        if self.pos.distance_to(self.target) <= self.snap_distance:
            self.pos = self.target.copy()
            self.vel = Vec2()
            self.active = False
            return True

        return False

    def _update_lerp(self, dt):
        to_target = self.target - self.pos
        dist = to_target.length()

        if dist <= self.snap_distance:
            self.pos = self.target.copy()
            self.vel = Vec2()
            self.active = False
            return True

        direction = to_target.normalized()

        # --- desired speed (ease OUT) ---
        desired_speed = self.max_speed
        if dist < self.slow_radius:
            desired_speed *= dist / self.slow_radius

        desired_velocity: Vec2 = direction * desired_speed # pyright: ignore

        # --- accelerate toward desired velocity (ease IN) ---
        steering: Vec2 = desired_velocity - self.vel
        max_change = self.acceleration * dt

        if steering.length() > max_change:
            steering = steering.normalized() * max_change

        self.vel += steering # pyright: ignore
        self.pos += self.vel * dt

        return False

    def _update_jump(self, dt):
        if self.vel == None: return
        # x moves toward target
        # direction_x = 1 if self.target.x > self.pos.x else -1
        # self.vel.x = direction_x * self.max_speed # normal option, where it just shoots at the max speed at that direction

        self.vel.x = self.target.x - self.pos.x  # moves to whatever x you need with whatever speed is required

        # gravity
        self.vel.y += self.gravity * dt

        self.pos += self.vel * dt

        return False
    
    def begin_drag(self, mouse_pos: Vec2):
        self.active = True
        self.movement_type = MovementType.DRAG
        self.pos = mouse_pos - self.drag_offset # initial snapping to cursor movement
        # print ("SNAP")
        self.vel = Vec2()

    def update_drag_target(self, mouse_pos: Vec2, dt):
        if self.movement_type != MovementType.DRAG:
            return

        screen = self.pet.primary_screen.availableGeometry()
        if (
            mouse_pos.x >= screen.width() - self.pet.hitbox_width / 2
            or mouse_pos.x <= self.pet.hitbox_width / 2
            or mouse_pos.y >= screen.bottom()
        ):
            self.end_drag()
            return
            
        self.angle = (self.angle + 180) % 360 - 180

        # --- Inject energy from mouse movement ---
        mouse_delta = mouse_pos.x - self.pos.x
        self.angular_vel += mouse_delta * math.cos(math.radians(self.angle)) * self.inertia # tweak multiplier

        # --- Damped spring physics ---
        angular_acc = -(self.gravity / 1) * math.sin(math.radians(self.angle)) - self.damping * self.angular_vel

        self.angular_vel += angular_acc * dt
        self.angle += self.angular_vel * dt

        # Clamp final rotation
        if self.max_angle < 360:
            self.angle = max(
                -self.max_angle,
                min(self.angle, self.max_angle)
            )

        # print(self.angle)

        self.pet.rotation_angle = self.angle

        self.pos = mouse_pos - self.drag_offset


    def end_drag(self):
        if self.movement_type == MovementType.DRAG:
            self.active = False
            self.movement_type = None
            self.pet.rotation_angle = 0
            self.angle = 0
            self.angular_vel = 0
            self.pet.state_machine.pulse(Pulse.DRAGGING_ENDED)
            self.pet.click_detector.release()
