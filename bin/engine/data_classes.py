from dataclasses import dataclass, field
from typing import NamedTuple

from PySide6.QtGui import QPixmap
from engine.enums import SurfaceNormal, Facing
from engine.vec2 import Vec2



# @dataclass(slots=True)
# class AnimationVariant:
#     frames: list[QPixmap]
#     weight: float

@dataclass(slots=True)
class AnimationData:
    """
    Contains all data needed to play animation: list of frames, fps, holds, loop, times_to_loop, bounds
    """
    frames: list[QPixmap]
    fps: float = 12
    holds: dict = field(default_factory=dict)
    loop: bool = False
    times_to_loop: int = 1
    bounds: tuple[int, int] = (0, 0)
    # variants: list[AnimationVariant] = field(default_factory=list)


@dataclass(slots=True)
class Hitbox:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center_x(self):
        return (self.left + self.right) / 2

    @property
    def center_y(self):
        return (self.top + self.bottom) / 2
    

class AllSurfacesData(NamedTuple):
    """
    Contains lists for all types of surfaces.
    """
    top: list
    right: list
    bottom: list
    left: list


class SegmentData(NamedTuple):
    """
    Contains the rect and lists for all types of segments for a window.
    """
    rect: tuple
    top: list[tuple]
    bottom: list[tuple]
    left: list[tuple]
    right: list[tuple]

@dataclass(slots=True)
class PetPositionData():
    """
    Contains the center and anchor positions
    """
    center: Vec2
    parent_surface_type: SurfaceNormal | None
    hitbox_width: int
    hitbox_height: int

    facing: Facing = Facing.RIGHT

    left:   float = 0
    top:    float = 0
    right:  float = 0
    bottom: float = 0

    @property
    def anchor_left(self):
        return Vec2(self.center.x - self.hitbox_width/2, self.center.y)
    
    @property
    def anchor_top(self):
        return Vec2(self.center.x, self.center.y - self.hitbox_height/2)
    
    @property
    def anchor_right(self):
        return Vec2(self.center.x + self.hitbox_width/2, self.center.y)
    
    @property
    def anchor_bottom(self):
        return Vec2(self.center.x, self.center.y + self.hitbox_height/2)
    
    @property
    def anchor(self) -> Vec2:
        match self.parent_surface_type:
            case SurfaceNormal.LEFT:
                return self.anchor_right
            case SurfaceNormal.UP:
                return self.anchor_bottom
            case SurfaceNormal.RIGHT:
                return self.anchor_left
            case SurfaceNormal.DOWN:
                return self.anchor_top

        return self.center
            
    def update_hitbox(self, new_width: int, new_height: int):
        self.hitbox_width = new_width
        self.hitbox_height = new_height

        offset_x: int = 0
        offset_y: int = 0

        match self.parent_surface_type:
            case SurfaceNormal.LEFT:
                offset_x = -1 * new_width // 2
            case SurfaceNormal.UP:
                offset_y = -1 * new_height // 2
            case SurfaceNormal.RIGHT:
                offset_x = new_width // 2
            case SurfaceNormal.DOWN:
                offset_y = new_height // 2

        center_x = self.center.x + offset_x
        center_y = self.center.y + offset_y
        
        self.center.x = center_x
        self.center.y = center_y

        self.left = center_x - new_width/2
        self.top = center_y - new_height/2
        self.right = center_x + new_width/2
        self.bottom = center_y + new_height/2