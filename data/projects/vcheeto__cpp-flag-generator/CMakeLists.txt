cmake_minimum_required(VERSION 3.16)
project(flaggen CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

find_package(PkgConfig REQUIRED)
pkg_check_modules(CAIRO REQUIRED IMPORTED_TARGET cairo)

find_package(nlohmann_json QUIET)

add_library(flaggen_core STATIC
    src/color.cpp
    src/config.cpp
    src/layers.cpp
    src/generator.cpp
)
target_include_directories(flaggen_core PUBLIC src)
target_link_libraries(flaggen_core PUBLIC PkgConfig::CAIRO)

if(NOT nlohmann_json_FOUND)
    target_include_directories(flaggen_core PUBLIC /usr/include)
endif()

add_executable(flaggen
    src/main.cpp
)
target_include_directories(flaggen PRIVATE src)
target_link_libraries(flaggen PRIVATE flaggen_core)

pkg_check_modules(GTK3 REQUIRED IMPORTED_TARGET gtk+-3.0)

add_executable(flaggen_gui
    src/gui.cpp
)
target_link_libraries(flaggen_gui PRIVATE flaggen_core PkgConfig::GTK3)
