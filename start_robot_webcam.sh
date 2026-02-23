#!/bin/bash

# 1. ROS 2 Umgebung laden
source /opt/ros/humble/setup.bash
source ~/ros2_ws/install/setup.bash

echo "🦾 Starte Roboter-Treiber (Mock/Simulation)..."
# Startet den Controller Manager und die Hardware-Schnittstelle
ros2 launch ur_robot_driver ur_control.launch.py \
    ur_type:=ur5e \
    robot_ip:=192.168.1.100 \
    use_fake_hardware:=true \
    launch_rviz:=false &
sleep 7

echo "🧠 Starte MoveIt & RViz..."
# Startet die Bewegungsplanung und die grafische Oberfläche
ros2 launch ur_moveit_config ur_moveit.launch.py \
    ur_type:=ur5e \
    launch_rviz:=true &
sleep 10

echo "👁️ Starte YOLO mit Webcam..."
# Startet dein Python-Skript (Webcam wird intern über cv2.VideoCapture(0) geladen)
python3 ~/ros2_ws/yolo_robot.py

# Wenn du das Python-Skript mit 'q' beendest, werden auch die Hintergrundprozesse gestoppt
trap "kill 0" EXIT
