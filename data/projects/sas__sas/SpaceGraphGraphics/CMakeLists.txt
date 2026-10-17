cmake_minimum_required(VERSION 3.16)
project(SpaceGraphGraphics CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# ---- Graphics engine dependencies -------------------------------------------
find_package(OpenGL REQUIRED)
find_package(PNG REQUIRED)
find_package(JPEG REQUIRED)

# FreeType
find_path(FREETYPE_INCLUDE_DIR freetype/freetype.h
          PATHS /usr/include /usr/include/freetype2 /usr/local/include)
if(NOT FREETYPE_INCLUDE_DIR)
  find_path(FREETYPE_INCLUDE_DIR ft2build.h
            PATHS /usr/include/freetype2 /usr/local/include/freetype2)
endif()
find_library(FREETYPE_LIBRARY NAMES freetype)
if(NOT FREETYPE_INCLUDE_DIR OR NOT FREETYPE_LIBRARY)
  message(FATAL_ERROR "FreeType headers/library not found")
endif()

# ---- Graphics engine --------------------------------------------------------
# Standalone graphics engine extracted from the SpaceGraph (space orbital
# debris) game: immediate-mode OpenGL 2D renderer + FreeType text + procedural
# planet/star textures + true-3D sphere rendering. No game logic, no SDL
# dependency (the caller owns the GL context / window).
add_library(sggraphics
  src/renderer/Renderer.cpp
  src/renderer/ImageLoader.cpp
  src/renderer/Sphere3D.cpp
  src/renderer/FontRenderer.cpp
  src/texturegen/PlanetTextureGen.cpp
  src/texturegen/TextureCache.cpp
)
target_include_directories(sggraphics PUBLIC
  include
  ${FREETYPE_INCLUDE_DIR}
)
target_link_libraries(sggraphics PUBLIC
  OpenGL::GL
  ${FREETYPE_LIBRARY}
  ${PNG_LIBRARIES}
  ${JPEG_LIBRARIES}
)

# ---- Example: SDL2 window demo ----------------------------------------------
# The engine itself is SDL-agnostic; the demo supplies an SDL2 window + GL 2.1
# context (the same setup the game uses) and exercises the renderer.
option(SG_BUILD_DEMO "Build the SDL2 demo application" ON)
if(SG_BUILD_DEMO)
  find_package(SDL2 REQUIRED)
  add_executable(sg_demo examples/demo.cpp)
  target_include_directories(sg_demo PRIVATE include)
  target_link_libraries(sg_demo PRIVATE sggraphics SDL2::SDL2)
endif()