#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "moveit_calibration_gui::moveit_handeye_calibration_rviz_plugin_core" for configuration "Release"
set_property(TARGET moveit_calibration_gui::moveit_handeye_calibration_rviz_plugin_core APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(moveit_calibration_gui::moveit_handeye_calibration_rviz_plugin_core PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libmoveit_handeye_calibration_rviz_plugin_core.so.2.0.0"
  IMPORTED_SONAME_RELEASE "libmoveit_handeye_calibration_rviz_plugin_core.so.2.0.0"
  )

list(APPEND _IMPORT_CHECK_TARGETS moveit_calibration_gui::moveit_handeye_calibration_rviz_plugin_core )
list(APPEND _IMPORT_CHECK_FILES_FOR_moveit_calibration_gui::moveit_handeye_calibration_rviz_plugin_core "${_IMPORT_PREFIX}/lib/libmoveit_handeye_calibration_rviz_plugin_core.so.2.0.0" )

# Import target "moveit_calibration_gui::moveit_handeye_calibration_rviz_plugin" for configuration "Release"
set_property(TARGET moveit_calibration_gui::moveit_handeye_calibration_rviz_plugin APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(moveit_calibration_gui::moveit_handeye_calibration_rviz_plugin PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libmoveit_handeye_calibration_rviz_plugin.so.2.0.0"
  IMPORTED_SONAME_RELEASE "libmoveit_handeye_calibration_rviz_plugin.so.2.0.0"
  )

list(APPEND _IMPORT_CHECK_TARGETS moveit_calibration_gui::moveit_handeye_calibration_rviz_plugin )
list(APPEND _IMPORT_CHECK_FILES_FOR_moveit_calibration_gui::moveit_handeye_calibration_rviz_plugin "${_IMPORT_PREFIX}/lib/libmoveit_handeye_calibration_rviz_plugin.so.2.0.0" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
