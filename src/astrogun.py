#!/usr/bin/python
from __future__ import absolute_import, division, print_function, unicode_literals
""" Wavefront obj model loading. Material properties set in mtl file.
Uses the import pi3d method to load *everything*
"""

import sys
sys.path.append('../src')
import pi3d
import time
import asteroids, bullets
import numpy, numpy.linalg
import util
import math
import RPi.GPIO as GPIO
import os.path
import pickle
from settings import *
import RTIMU
import threading
import pygame.mixer

######################################
#### GameLevel
######################################

# Operating modes
MODE_READY    = 0
MODE_READY_GO = 1
MODE_GO       = 2
MODE_GO_OUT   = 3
MODE_PLAY     = 4

class IMUReader(threading.Thread):
  def __init__(self, imu):
    threading.Thread.__init__(self)
    self.imu = imu
    self.data = (0, 0, 0)
    self.running = True;
    self.wait_s = imu.IMUGetPollInterval()*1.0/1000.0
    
  def run(self):
    while(self.running):
      if self.imu.IMURead():
        self.data = self.imu.getFusionData()
      time.sleep(self.wait_s)


class GameLevel:
  def __init__(self, sprites):
    # Instantiate an Asteroid Generator
    self.gen = asteroids.AsteroidGenerator(ASTEROIDS, 0.1, None, shader_explosion,
                                           shader_uv_flat)
    self.bullet_gen = bullets.BulletGenerator()
    self.active_asteroids = {}
    self.asteroid_id = 0
    self.active_bullets = []
    self.hit_asteroids = []
    self.azimuth = 0.0
    self.incl = 0.0
    self.self_hit = -1
    self.sprites = sprites
    self.fixed_sprites = []
    self.lives = INITIAL_LIVES
    self.scores = 0
    self.scores_changed = True
    self.pause = False
    self.free_play = False
    self.fire_button_state = 1
    self.frames = 0
    self.mode = [MODE_READY, READY_TIME]
    self.ready_text = pi3d.String(font=FONT_BALLS, 
                                  string = "READY?",
                                  x = -.3, y = 1, z = 3.9,
                                  sx=0.018, sy=0.018)
    self.ready_text.set_shader(shader_uv_flat)
    self.go_text = pi3d.String(font=FONT_BALLS, 
                               string = "GO!",
                               x = -.2, y = 0.8, z = 3.9 + 5*.5,
                               sx=0.018, sy=0.018)
    self.go_text.set_shader(shader_uv_flat)
    
    # Initial sprite location
    s = self.sprites['sight']
    s.position(*SIGHT_POSITION)
    s.scale(*SIGHT_SCALE)
    self.fixed_sprites.append(s)

    s = sprites['radar_panel']
    s.position(*RADAR_PANEL_POSITION)
    s.scale(*RADAR_PANEL_SCALE)
    self.fixed_sprites.append(s)
    
    self.radar_target = sprites['radar_target']
    self.radar_target.scale(*TARGET_SCALE)
    
    self.life_full = sprites['life_full']
    self.life_full.scale(*LIFE_BAR_SCALE)

    self.life_empty = sprites['life_empty']
    self.life_empty.scale(*LIFE_BAR_SCALE)

  def create_bullet(self, now):
    b = self.bullet_gen.generate(self.azimuth, self.incl, now)
    self.active_bullets.append(b)
    SOUNDS['shot'].play()
    
    # For all asteroids, check if the bullet hits them
    I = b.get_direction()
    indx = 0
    dest = None
    
    # Scan all the asteroids against incidence with the newly
    # created bullet. If more than one asteroid incides with
    # the bullet trajectory, pick the closest one
    for astid, ast in self.active_asteroids.items():
      if (self.check_incidence(ast, I)):
        if dest is None:
          dest = (astid, ast)
        else:
          if (ast.distance2() < dest[1].distance2()):
            dest = (astid, ast)
        
    b.set_destination(dest)

  # Check wheter a bullet will hit an asteroid. 
  # asteroid - An Asteroid class object
  # bullet - A unit vector designating the bullet direction
  #
  # The test is based on a line-sphere intersection test, as described
  # in http://en.wikipedia.org/wiki/Line%E2%80%93sphere_intersection
  # We are not interested in the full solution of the equation, only whether
  # the term under square root is non-negative. Also, the bullets always
  # originate at the origin (0,0,0) simplifying the equation further
  def check_incidence(self, asteroid, bullet):
    c = asteroid.get_position()
    r = asteroid.radius
    I = bullet
    
    sq = (I.dot(c))**2 - (I.dot(I)*(c.dot(c) - r**2))
    return (sq >= 0)

  def play(self, keys):
    now = time.time()
    start_time = now
    imux = 0
    imuy = 0
    imuz = 0
    
    while DISPLAY.loop_running():
      now = time.time()
      self.frames += 1
      
      # Self hit effect
      if self.self_hit > 0:
        DISPLAY.set_background(self.self_hit*1.0/10.0, 0, 0, 1)
        if self.self_hit < 10:
          self.self_hit += 1
        else:
          self.self_hit = -1
          DISPLAY.set_background(0.0,0,0,1.0)
          
      # (possibly) generate a new asteroid
      if not self.pause:
        ast = self.gen.generate_asteroid(now)
        if ast is not None:
          self.active_asteroids[self.asteroid_id] = ast
          self.asteroid_id += 1
    
      # Draw all active asteroid
      for astid, ast in self.active_asteroids.items():
        # Draw the asteroid itseld
        if not self.pause:
          ast.move(now)
        dist2_from_origin = ast.distance2()
        
        # Draw the target on the radar view
        dist_from_origin = (math.sqrt(dist2_from_origin)/INITIAL_DISTANCE)*TARGET_DIST_SCALE
        angle = math.radians(ast.azimuth + self.azimuth + 90)
        rtx = dist_from_origin*math.cos(angle)
        rty = dist_from_origin*math.sin(angle)
        self.radar_target.position(TARGET_CENTER_POSITION[0]+rtx, 
                                   TARGET_CENTER_POSITION[1]+rty,
                                   TARGET_CENTER_POSITION[2])
        self.radar_target.draw(camera = cam2d)

        if dist2_from_origin < SELF_IMPACT_RADIUS2:
          # Reached origin, destory it
          self.gen.return_asteroid(self.active_asteroids[astid])
          del self.active_asteroids[astid]
          self.self_hit = 1
          SOUNDS['self_hit'].play()
          if not self.free_play:
            self.lives -= 1
      
        # Position, rotate and draw the asteroid
        ast.draw(camera = cam3d)

      # Delete all hit asteroids, whose time has passed
      for astid in range(len(self.hit_asteroids)):
        print (astid)
        print(self.hit_asteroids)
        ast = self.hit_asteroids[astid]
        if ast.hit_time > 8.0:
          self.gen.return_asteroid(self.hit_asteroids[astid])
          del self.hit_asteroids[astid]

      # Draw all hit asteroids
      for ast in self.hit_asteroids:
        ast.move(now)
        if ast.hit_time > 8.0:
          self.hit_asteroids[0]
          
        ast.draw(camera = cam3d)

      # Draw all active bullets
      objindex = 0
      for bull in self.active_bullets:
        if not self.pause:
          bull.move(now)
        dest = bull.get_destination()
        dist2_from_origin = bull.distance2()
        
        if (dest is not None) and (dest[0] in self.active_asteroids):
          ast_distance2 = dest[1].distance2()
          if dist2_from_origin > ast_distance2:
            # Bullet hit the asteroid

            del self.active_asteroids[dest[0]]
            dest[1].hit(now)
            self.hit_asteroids.append(dest[1])
            del self.active_bullets[objindex]
            self.scores += 1
            self.scores_changed = True
            SOUNDS['astro_hit'].play()
            
        elif dist2_from_origin > BULLET_DISTANCE2:
          # Reached final distance, destroy it
          del self.active_bullets[objindex]
        else:
          objindex += 1
      
        bull.draw(camera = cam3d)

      # Draw Sprites
      for s in self.fixed_sprites:
        s.draw(camera = cam2d)
        
      # Draw lives
      for l in range(0, 5):
        if l+1 > self.lives:
          s = self.life_empty
        else:
          s = self.life_full
        s.position(LIFE_BAR_POSITION[0],
                   LIFE_BAR_POSITION[1] + l*LIFE_BAR_STEP,
                   LIFE_BAR_POSITION[2])
        s.draw(camera = cam2d)

      # Draw scores
      if self.scores_changed:
        self.scores_str = pi3d.String(font=FONT_COMPUTER, 
                                      string="%03d" % self.scores,
                                      x = SCORE_POSITION[0],
                                      y = SCORE_POSITION[1],
                                      z = SCORE_POSITION[2],
                                      sx=0.01, sy=0.01)
        self.scores_str.set_shader(shader_uv_flat)
        scores_changed = False

      self.scores_str.draw(camera = cam2d)

      # Draw READY-GO text
      if (self.mode[0] == MODE_READY):
        self.ready_text.draw(camera = cam2d)
        self.mode[1] -= 1
        if (self.mode[1] == 0):
          self.mode = [MODE_READY_GO, 5]
          
      elif (self.mode[0] == MODE_READY_GO):
        self.ready_text.translateZ(.5)
        self.ready_text.set_custom_data(17, [self.mode[1]/5.0])
        self.ready_text.draw(camera = cam2d)
        self.go_text.translateZ(-0.5)
        self.go_text.set_custom_data(17, [1.0 - self.mode[1]/5.0])
        self.go_text.draw(camera = cam2d)
        self.mode[1] -= 1
        if (self.mode[1] == 0):
          self.mode = [MODE_GO, GO_TIME]
          
      elif (self.mode[0] == MODE_GO):
        self.go_text.draw(camera = cam2d)
        self.mode[1] -= 1
        if (self.mode[1] == 0):
          self.mode = [MODE_GO_OUT, 5]

      elif (self.mode[0] == MODE_GO_OUT):
        self.go_text.translateZ(.5)
        self.go_text.set_custom_data(17, [self.mode[1]/5.0])
        self.go_text.draw(camera = cam2d)
        self.go_text.draw(camera = cam2d)
        self.mode[1] -= 1
        if (self.mode[1] == 0):
          self.mode = [MODE_PLAY, 0]        
      
      # Debugging
      #debug_str = "az: %f incl: %f" % (self.azimuth, self.incl)
      #debug_str_pi = pi3d.String(font=FONT_ARIAL, string=debug_str,
      #                           x = 0, y = 0, z = 5, sx=0.005, sy=0.005)
      #debug_str_pi.set_shader(shader_uv_flat)
      #debug_str_pi.draw(camera = cam2d)

      # Read the IMU angles
      imux, imuy, imuz = IMU.data
      self.incl = -math.degrees(imuy)
      self.azimuth = math.degrees(imuz)
      cam_rotate = True
      
      # TEMPORARY CODE
      k = keys.read()
      cam_rotate = False
      if k >-1:
        if k == ord('p'):
          # Toggle pause
          self.pause = not self.pause
        
        elif k == ord('f'):
          # Toggle free play mode
          self.free_play = not self.free_play
          
        elif k==ord(' '):
          self.create_bullet(now)
        elif (k == 27):
          break
      
      # Check if the trigger button is pressed
      fire_button = GPIO.input(BUTTON_FIRE_GPIO[0]) 
      if (fire_button == 1 and self.fire_button_state == 0):
        self.create_bullet(now)
        pass
      self.fire_button_state = fire_button
      
      # Handle camera rotation
      if True: #cam_rotate:
        cam3d.reset()
        cam3d.rotateX(self.incl)
        cam3d.rotateY(-self.azimuth)

      # If no more lives left, terminate the game
      if self.lives == 0:
        break
      
    # Calculate average FPS
    end_time = time.time()
    self.FPS = (1.0*self.frames)/(1.0*(end_time - start_time))


######################################
#### OpeningScreen
######################################
class OpeningScreen:
  
  def __init__(self):
    # Position the openinig screen graphics
    self.spr = SPRITES['opening']
    self.spr.position(0, 0, 4)
    self.spr.scale(3.7, 3.7, 1)
    
    self.text = pi3d.String(font=FONT_COMPUTER, 
                            string = "Press the START Button to Begin",
                            x = 0, y = .5, z = 3.9,
                            sx=0.005, sy=0.005)
    self.text.set_shader(shader_uv_flat)
    self.text_ts_delta = 0.1
    self.text_ts = 0
    
  def start(self):
    while DISPLAY.loop_running():
      # Draw the opening screen
      self.spr.draw(camera = cam2d)

      self.text_ts += self.text_ts_delta
      #if self.text_ts > 1.0:
      #  self.text_ts = 1.0
      #  self.text_ts_delta = -self.text_ts_delta

      #if self.text_ts < 0.0:
      #  self.text_ts = 0.0
      #  self.text_ts_delta = -self.text_ts_delta

      self.text.set_custom_data(17, [abs(math.sin(self.text_ts))])

      self.text.draw(camera = cam2d)

      # Check if the START button was pressed
      b = GPIO.input(BUTTON_START_GPIO)
      if (b == 0):
        break
      
      k = KEYS.read()
      if k >-1:
        break;


def load_sprites():
  sprite_filenames = ['sight', 'radar_panel', 'radar_target', 'life_full', 'life_empty', 'trans', 'opening']
  sprites = {}
  sh = shader_uv_flat
  
  for fn in sprite_filenames:
    s = pi3d.ImageSprite('../media/bitmaps/' + fn + '.png', shader = sh, w = 1, h = 1)
    sprites[fn] = s
    
  return sprites


def setup_io():
  GPIO.setmode(GPIO.BCM)
  GPIO.setup(BUTTON_START_GPIO, GPIO.IN, GPIO.PUD_UP)
  GPIO.setup(BUTTON_FIRE_GPIO[0], GPIO.IN, GPIO.PUD_UP)
  GPIO.setup(BUTTON_FIRE_GPIO[1], GPIO.OUT)
  GPIO.output(BUTTON_FIRE_GPIO[1], 0)
  GPIO.setup(RUMBLE_FIRE_GPIO, GPIO.OUT)

def load_asteroids():
  # Check if a pre-loaded database exists
  db_filename = os.path.join(VAR_DIR, AST_DB_FILENAME)
  start = time.time()
  
  if os.path.exists(db_filename):
    # Load the database
    ast = pickle.load(file(db_filename, 'rb'))
    
  else:
    # Database does not exist. Load the models then save
    # the database
    ast = []
    global_scale = 1.0
    for mf in asteroids.models[0:5]:
      model_filename = mf[0]
      model_scale = mf[1]
      model_name = model_filename.split('.')[0] # Remove the .obj extention
      
      m = pi3d.Model(file_string='../media/models/' + model_filename, 
                     name=model_name)
      m.scale(model_scale*global_scale, 
              model_scale*global_scale,
              model_scale*global_scale)
  
      ast.append(m)
      
    pickle.dump(ast, file(db_filename, 'wb'))
    
  # Set the shader for all models
  for a in ast:
    a.set_shader(shader_uv_flat)
    
  end = time.time()
  print("Loading time: %f\n" % (end-start))
  return ast

def init_imu():
  s = RTIMU.Settings("RTIMU")
  imu = RTIMU.RTIMU(s)
  print("IMU Name: " + imu.IMUName())
  
  if (not imu.IMUInit()):
    print("IMU Init Failed");
    sys.exit(1)
  else:
    print("IMU Init Succeeded");
    
  reader = IMUReader(imu)
  reader.start()
  return reader

def init_sounds():
  # Init the mixer
  pygame.mixer.init()
  
  # Load sounds
  sounds =  {
    'win': pygame.mixer.Sound(SOUNDS_DIR + '126000__xserra__campeones.wav'),
    'shot': pygame.mixer.Sound(SOUNDS_DIR + '156895__halgrimm__a-shot.wav'),
    'self_hit': pygame.mixer.Sound(SOUNDS_DIR + '218721__bareform__boom-bang.wav'),
    'astro_hit': pygame.mixer.Sound(SOUNDS_DIR + '147584__cactus2003__far-off-boom.wav'),
    'lose': pygame.mixer.Sound(SOUNDS_DIR + '178875__rocotilos__you-lose-evil.wav')
    }
  
  return sounds

# Setup display and initialise pi3d
DISPLAY = pi3d.Display.create(background=(0.0, 0, 0, 1))
DISPLAY.frames_per_second = 30

# Create Cameras
ASPECT = DISPLAY.width / DISPLAY.height
cam3d = pi3d.Camera((0,0,0), (0,0,-0.1), (1, 1000, 45, ASPECT), is_3d=True)
cam2d = pi3d.Camera(is_3d=True)

# Load shaders
shader_uv_flat = pi3d.Shader('uv_flat')
shader_mat_flat = pi3d.Shader('mat_flat')
shader_explosion = pi3d.Shader("uv_flat_explode")

# Load Fonts
FONT_ARIAL = pi3d.Font("../media/fonts/FreeMonoBoldOblique.ttf", (221,0,170,255))
FONT_COMPUTER = pi3d.Font("../media/fonts/Computerfont.ttf", (0,0,255,255))
FONT_BALLS = pi3d.Font("../media/fonts/BallsoOnTheRampage.ttf", (50,70,120,255))

# Load Sprites
SPRITES = load_sprites()

# Load Asteroid models
ASTEROIDS = load_asteroids()

# Load sounds
SOUNDS = init_sounds()

# Setup I/O
setup_io()

# Initialize the IMU
IMU = init_imu()

# Fetch key presses
KEYS = pi3d.Keyboard()

opening = OpeningScreen()
opening.start()
  
level = GameLevel(SPRITES)
try:
  level.play(KEYS)
  
  KEYS.close()
  DISPLAY.destroy()
  IMU.running = False

except:
  #mykeys.close()
  DISPLAY.destroy()
  IMU.running = False
  print(level.gen.asteroid_model_list)
  raise

IMU.running = False


