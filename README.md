Vision-Based Cobot Pick-and-Place Framework

🚀 Open-source framework for vision-guided robotic automation using AI and ROS2

⸻

Overview

This repository provides an open-source framework for vision-based robotic pick-and-place using collaborative robots (cobots). It integrates computer vision, AI-based object detection, and robotic control to enable flexible and adaptive automation workflows.

The system is designed for research and industrial prototyping, focusing on reproducibility, modularity, and real-world applicability.

⸻

Features
	•	Real-time object detection using AI models
	•	Camera calibration and coordinate transformation
	•	ROS2-based robot control pipeline
	•	Modular architecture for easy integration
	•	Support for simulation and real robot execution
	•	Automated workflow scripts

⸻

Repository Structure

calibration/ → Camera calibration and transformation
config/ → Configuration files
core/ → Core logic (pipeline, control)
models/ → AI / vision models
ros2_ws/ → ROS2 workspace
start_real_robot.sh → Start real robot execution
upload_to_cloud.sh → Cloud integration utility

⸻

System Architecture

Vision Module
	•	Object detection
	•	Image processing
	•	Feature extraction

Transformation Layer
	•	Camera-to-robot calibration
	•	Coordinate mapping

Robot Control
	•	Pick-and-place logic
	•	Motion execution via ROS2

⸻

Installation

Requirements
	•	Python 3.x
	•	ROS2 (Humble or newer recommended)
	•	OpenCV
	•	PyTorch or TensorFlow

Setup
git clone 
cd 
pip install -r requirements.txt
cd ros2_ws
colcon build

⸻

Usage

Start system
./start_real_robot.sh

Calibration
Use scripts inside the calibration folder

⸻

Use Cases
	•	Vision-guided robotic manipulation
	•	Flexible manufacturing systems
	•	AI + robotics research
	•	Automation prototyping

⸻

Roadmap
	•	Improved grasp planning
	•	Multi-object detection
	•	Simulation integration
	•	Web interface
	•	Performance optimization

⸻

Contribution

Contributions are welcome. Open issues or submit pull requests.

⸻

Author

Maintained as part of a research-oriented open-source robotics project.