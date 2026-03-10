#!/bin/bash
# Get the actual directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# 1. ROS 2 Umgebung laden
source /opt/ros/humble/setup.bash
# Falls es im Workspace ein install-Verzeichnis gibt:
# (Wir gehen 3 Ebenen hoch vom 'aivision' Ordner: aivision -> robot_pose_pipeline -> src -> pylon_ws)
source "$SCRIPT_DIR/../../../install/setup.bash"
# HandEye Calibration Plugins Workspace laden:
if [ -f "$SCRIPT_DIR/ros2_ws/install/setup.bash" ]; then
    source "$SCRIPT_DIR/ros2_ws/install/setup.bash"
fi

echo "🦾 Starte ECHTEN Roboter-Treiber..."
# WICHTIG: Ersetze 192.168.56.101 mit der echten IP-Adresse deines Roboters!
ROBOT_IP="192.168.56.101"

# Startet den Controller Manager und die Hardware-Schnittstelle
# HINWEIS: Da der echte Roboter nicht verbunden ist, nutzen wir vorübergehend fake_hardware
ros2 launch ur_robot_driver ur_control.launch.py \
    ur_type:=ur5e \
    robot_ip:=$ROBOT_IP \
    use_fake_hardware:=true \
    launch_rviz:=false &
sleep 10

echo "🧠 Starte MoveIt & RViz..."
export QT_QPA_PLATFORM=xcb
export LIBGL_ALWAYS_SOFTWARE=1

# Startet die Bewegungsplanung und die grafische Oberfläche
ros2 launch "$SCRIPT_DIR/config/my_ur_moveit.launch.py" \
    ur_type:=ur5e \
    launch_rviz:=true &
sleep 10

echo "👁️ Starte YOLO mit Webcam (Modern GUI Version)..."
# Startet dein Python-Skript aus dem AKTUELLEN Verzeichnis
python3 "$SCRIPT_DIR/core/main.py"

# Wenn du das Python-Skript mit 'q' beendest, werden auch die Hintergrundprozesse gestoppt
trap "kill 0" EXIT
