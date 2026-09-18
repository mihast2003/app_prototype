import random, math

from engine.vec2 import Vec2

from engine.enums import EmitterShape
from engine.data_classes import PetTransformData

class IntProvider():
    def __init__(self, cfg):
        self.cfg = cfg

    def get(self) -> int:  
        """
        Returns the value of the variable
        
        int
        """      
        if not isinstance(self.cfg, dict):
            return self.cfg

        if "constant" in self.cfg:
            return self.cfg["constant"]

        if "random_range" in self.cfg:
            return int(random.uniform(*self.cfg["random_range"]))

        if "random_from_list" in self.cfg:
            return random.choice(self.cfg["random_from_list"])
        
        return 0
    
class FloatProvider():
    def __init__(self, cfg):
        self.cfg = cfg

    def get(self) -> float:
        """
        Returns the value of the variable
        
        float
        """          
        if not isinstance(self.cfg, dict):
            return self.cfg

        if "constant" in self.cfg:
            return self.cfg["constant"]

        if "random_range" in self.cfg:
            return random.uniform(*self.cfg["random_range"])

        if "random_from_list" in self.cfg:
            return random.choice(self.cfg["random_from_list"])
        
        return 0


class ParticleEmitter:
    def __init__(self, particleSystem, name, cfg, pet_transform: PetTransformData):
        self.particleSystem = particleSystem

        self.name = name
        self.cfg: dict = cfg

        self.pet = pet_transform

        self.hitbox_x = pet_transform.hitbox_width 
        self.hitbox_y = pet_transform.hitbox_height

        self.time = 0.0
        self.emitted = 0
        self.elapsed = 0
        self.done_emitting = False
        self.next_emit_time = 0.0

        shape = cfg.get("emitter_shape")
        self.emitter_shape = EmitterShape.__members__.get(shape, EmitterShape.DOT)

        self.anchor_x, self.anchor_y = self.pet.anchor

        self.offset_x = FloatProvider(cfg.get("emitter_offset", (0,0))[0])
        self.offset_y = FloatProvider(cfg.get("emitter_offset", (0,0))[1])

        self.lifetime = FloatProvider(self.cfg.get("lifetime", 1))

        self.rate = FloatProvider(self.cfg.get("rate_over_time", 5))

        self.random_timing = FloatProvider(self.cfg.get("random_timing", 0))

        self.total_count = IntProvider(self.cfg.get("total_count", 1000))

        self.duration = FloatProvider(self.cfg.get("duration", 1))

        self.start_size = FloatProvider(self.cfg.get("start_size", 1))

        self.radius = FloatProvider(self.cfg.get("radius", 1))

        self.hollow: bool = self.cfg.get("hollow", False)

        self.border_x = FloatProvider(self.cfg.get("modify_border", (0,0))[0]) # get proportions
        self.border_y = FloatProvider(self.cfg.get("modify_border", (0,0))[1]) # get proportions

        # self.expand_x = self.hitbox_x * border_x.get() # convert to relative distances
        # self.expand_y = self.hitbox_y * border_y.get() / 2 # convert to relative distances

        self.circlage = FloatProvider(self.cfg.get("round_square", 0))

        self.start_vel_x = FloatProvider(self.cfg.get("start_vel", (0,0))[0])
        self.start_vel_y = FloatProvider(self.cfg.get("start_vel", (0,0))[1])
        # print("start_vel_y", self.start_vel_y.get())

        self.start_acc_x = FloatProvider(self.cfg.get("start_acceleration", (0,0))[0])
        self.start_acc_y = FloatProvider(self.cfg.get("start_acceleration", (0,0))[1])
        
        self.emit_top = self.cfg.get("emit_top", True)
        self.emit_bottom = self.cfg.get("emit_bottom", True)
        self.emit_left = self.cfg.get("emit_left", True)
        self.emit_right = self.cfg.get("emit_right", True)

    def update(self, dt):
        if self.done_emitting: return

        self.time += dt

        while self.time >= self.next_emit_time:
            self.spawn_particle()
            self.emitted += 1

            self.next_emit_time += self.get_emit_interval()

            if self.emitted >= self.total_count.get():
                break

        if self.emitted >= self.total_count.get() or self.time >= self.duration.get():
            # print(f"ParticleEmitter is done because {self.emitted} >= {self.total_count.get()} or {self.time} >= {self.duration.get()}")
            self.done_emitting = True

    def get_emit_interval(self):
        interval = 1.0 / self.rate.get()

        if self.random_timing.get():
            jitter = (random.random() - 0.5) * interval * self.random_timing.get()
            interval += jitter

        return max(0.00001, interval)


    def spawn_particle(self):
        # randomize vel, lifetime, etc
        anchor_x = self.anchor_x
        anchor_y = self.anchor_y

        pos_x: float
        pos_y: float

        vel_x = self.start_vel_x.get()
        vel_y = -self.start_vel_y.get()

        acc_x = self.start_acc_x.get()
        acc_y = -self.start_acc_y.get()

        hollow = self.hollow
        circlage = self.circlage

        expand_x = self.hitbox_x * self.border_x.get()
        expand_y = self.hitbox_y * self.border_y.get() / 2

        hitbox_x = self.hitbox_x
        hitbox_y = self.hitbox_y

        offset_x = self.offset_x.get()
        offset_y = self.offset_y.get() 
        emitter_offset_x = hitbox_x * -offset_x
        emitter_offset_y = hitbox_y * offset_y

        size = self.start_size.get()

        # print("shape", self.emitter_shape)

        match self.emitter_shape:
            case EmitterShape.DOT:
                pos_x = anchor_x - emitter_offset_x
                pos_y = anchor_y - emitter_offset_y
            case EmitterShape.LINE:
                point1 = Vec2(self.cfg.get("point1", (0,0)))
                point2 = Vec2(self.cfg.get("point2", (0,0)))
                if not point1 or not point2:
                    print("NO POINTS TO FORM A LINE, CHECK CONFIG")

                t = random.random()
                x1, y1 = point1
                x2, y2 = point2

                x = x1 + t * (x2 - x1)
                y = y1 + t * (y2 - y1)

                pos_x = anchor_x - (-x) * hitbox_x
                pos_y = anchor_y - y * hitbox_y

            case EmitterShape.CIRCLE:
                theta = random.uniform(0, 2 * math.pi)

                center_x = anchor_x - emitter_offset_x
                center_y = anchor_y - emitter_offset_y

                r = self.radius.get() * (hitbox_x + hitbox_y)/2

                if not hollow:
                    r *= math.sqrt(random.random())

                pos_x = center_x + r * math.cos(theta)
                pos_y = center_y + r * math.sin(theta)
            
            case EmitterShape.HITBOX:
                center_x = self.pet.anchor.x - emitter_offset_x
                center_y = self.pet.anchor.y - emitter_offset_y

                rand_x = random.random()
                rand_y = random.random()

                x = center_x + ((hitbox_x - expand_x) * rand_x) - (hitbox_x - expand_x)/2
                y = center_y - ((hitbox_y + expand_y) * rand_y)

                hollow: bool = self.cfg.get("hollow", False)
                if hollow:
                    pass
                
                pos_x = x
                pos_y = y

            case EmitterShape.RECTANGLE:
                corner1 = Vec2(anchor_x - hitbox_x/2 - expand_x - emitter_offset_x, anchor_y + expand_y - emitter_offset_y)

                corner2 = Vec2(anchor_x - hitbox_x/2 - expand_x - emitter_offset_x, anchor_y - hitbox_y - expand_y - emitter_offset_y)

                corner3 = Vec2(anchor_x + hitbox_x/2 + expand_x - emitter_offset_x, anchor_y - hitbox_y - expand_y - emitter_offset_y)

                corner4 = Vec2(anchor_x + hitbox_x/2 + expand_x - emitter_offset_x, anchor_y + expand_y - emitter_offset_y)

                rec_width: Vec2 = corner3 - corner2 
                rec_height: Vec2 = corner2 - corner1

                sides_list = []

                if hollow:
                    if self.emit_left:
                        sides_list.append({"vec": rec_height, "orig": corner1})
                    if self.emit_top:
                        sides_list.append({"vec": rec_width, "orig": corner2})
                    if self.emit_right:
                        sides_list.append({"vec": rec_height, "orig": corner4})
                    if self.emit_bottom:
                        sides_list.append({"vec": rec_width, "orig": corner1})

                    line = random.choice(sides_list)
                    r = random.random()
                    point = line["orig"] + (line["vec"] * r)

                else:
                    point = corner1 + (rec_width * random.random()) + (rec_height * random.random())


                if circlage is None:
                    pos_x, pos_y = point
                else:                # math for circling a square doesnt work because its not a unit circle and not a circle at all
                    px = point.x
                    py = point.y
                    cx = anchor_x - emitter_offset_x 
                    cy = anchor_y - rec_height.length()/2 - emitter_offset_y
                    width = rec_width.length()
                    height = rec_height.length()

                    # vector to to center
                    x = px - cx
                    y = py - cy

                    a = width / 2
                    b = height / 2

                    t_rect = min(
                        (a / abs(x)) if x != 0 else float("inf"),
                        (b / abs(y)) if y != 0 else float("inf")
                    )

                    t_ellipse = 1.0 / math.sqrt((x*x)/(a*a) + (y*y)/(b*b))

                    t_interp = (1 - circlage.get()) * t_rect + circlage.get() * t_ellipse

                    if not hollow and t_interp > 1:
                        xw, yw = point.x, point.y
                    else:
                        # warped point
                        xw = cx + x * t_interp
                        yw = cy + y * t_interp

                    
                    pos_x = xw
                    pos_y = yw

        # print(f"emiiting a particle:\n pos {pos_x} {pos_y}\n size {size}\n name {self.name}\n vel {vel_x} {vel_y}\n acc {acc_x} {acc_y}")

        self.particleSystem.emit_particle(
            pos_x=pos_x,
            pos_y=pos_y,
            vel_x=vel_x,
            vel_y=vel_y,
            acc_x=acc_x,
            acc_y=acc_y,
            name=self.name,
            size = size,
        )