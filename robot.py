import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from geometry_msgs.msg import Pose, PoseStamped, Quaternion, TransformStamped
from sensor_msgs.msg import JointState, PointCloud2, PointField
from shape_msgs.msg import SolidPrimitive
from moveit_msgs.msg import RobotState, Constraints, PositionConstraint, OrientationConstraint, BoundingVolume, JointConstraint
from moveit_msgs.action import MoveGroup, ExecuteTrajectory
from moveit_msgs.srv import GetCartesianPath, GetPositionFK
from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster
import math
import time
import copy
import struct

# --- KONSTANTEN ---
CAMERA_X = 0.50      
CAMERA_Y = 0.00
CAMERA_Z = 0.60
FOV_WIDTH_M = 0.30   
IMG_WIDTH = 640
HOVER_DISTANCE = 0.15   

class RobotController(Node):
    def __init__(self):
        super().__init__('industrial_robot_node')
        
        # --- CLIENTS ---
        self._move_group_client = ActionClient(self, MoveGroup, 'move_action')
        self._cartesian_client = self.create_client(GetCartesianPath, 'compute_cartesian_path')
        self._execute_client = ActionClient(self, ExecuteTrajectory, 'execute_trajectory')
        self._fk_client = self.create_client(GetPositionFK, 'compute_fk')
        
        # --- PUBLISHER ---
        self.sub_joints = self.create_subscription(JointState, '/joint_states', self.joint_cb, 10)
        self.cloud_pub = self.create_publisher(PointCloud2, 'video_cloud', 10)
        
        # HIER WAR DAS PROBLEM: Der Target Publisher fehlte!
        self.target_pub = self.create_publisher(PoseStamped, '/target_pose', 10)
        
        self.tf_static_broadcaster = StaticTransformBroadcaster(self)
        
        # --- STATE ---
        self.joint_state = None
        self.meters_per_pixel = FOV_WIDTH_M / IMG_WIDTH
        self.current_target_msg = None # Speichert den aktuellen Pfeil für RViz
        
        # Timer ersetzt den alten while-Loop für konstantes Publishing
        self.create_timer(0.1, self.publish_target_loop)

        self.publish_static_tf()
        print("🤖 Warte auf Roboter-Verbindung...")

    def joint_cb(self, msg):
        self.joint_state = msg

    def publish_target_loop(self):
        """Sorgt dafür, dass der Pfeil in RViz sichtbar bleibt"""
        if self.current_target_msg is not None:
            # Zeitstempel aktualisieren, damit RViz nicht meckert
            self.current_target_msg.header.stamp = self.get_clock().now().to_msg()
            self.target_pub.publish(self.current_target_msg)

    def publish_static_tf(self):
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = "base_link"
        t.child_frame_id = "camera_optical_frame"
        t.transform.translation.x = float(CAMERA_X)
        t.transform.translation.y = float(CAMERA_Y)
        t.transform.translation.z = float(CAMERA_Z)
        t.transform.rotation.x = 0.707
        t.transform.rotation.y = -0.707
        t.transform.rotation.z = 0.0
        t.transform.rotation.w = 0.0
        self.tf_static_broadcaster.sendTransform(t)

    def publish_video_cloud(self, frame):
        import cv2
        scale = 0.25
        small_frame = cv2.resize(frame, (0,0), fx=scale, fy=scale)
        height, width = small_frame.shape[:2]
        center_x = width / 2; center_y = height / 2; mpp_scaled = self.meters_per_pixel / scale
        
        buffer = bytearray()
        for v in range(height):
            for u in range(width):
                b, g, r = small_frame[v, u]
                rgb = struct.unpack('I', struct.pack('BBBB', b, g, r, 255))[0]
                rgb_float = struct.unpack('f', struct.pack('I', rgb))[0]
                dx_px = u - center_x; dy_px = v - center_y 
                x_world = CAMERA_X - (dy_px * mpp_scaled); y_world = CAMERA_Y - (dx_px * mpp_scaled); z_world = 0.005 
                buffer.extend(struct.pack('ffff', x_world, y_world, z_world, rgb_float))
        
        msg = PointCloud2(); msg.header.stamp = self.get_clock().now().to_msg(); msg.header.frame_id = "base_link"
        msg.height = 1; msg.width = width * height
        msg.fields = [PointField(name='x', offset=0, datatype=PointField.FLOAT32, count=1), PointField(name='y', offset=4, datatype=PointField.FLOAT32, count=1), PointField(name='z', offset=8, datatype=PointField.FLOAT32, count=1), PointField(name='rgb', offset=12, datatype=PointField.FLOAT32, count=1)]
        msg.is_bigendian = False; msg.point_step = 16; msg.row_step = msg.point_step * msg.width; msg.is_dense = True; msg.data = buffer
        self.cloud_pub.publish(msg)

    def execute_trajectory(self, trajectory):
        goal = ExecuteTrajectory.Goal(); goal.trajectory = trajectory
        self._execute_client.wait_for_server()
        future = self._execute_client.send_goal_async(goal)
        # Nicht blockieren, sonst friert GUI ein, wir lassen es im Hintergrund laufen
        return True

    def move_ptp(self, pose):
        try:
            goal = MoveGroup.Goal(); goal.request.workspace_parameters.header.frame_id = "base_link"; goal.request.group_name = "ur_manipulator"
            constraints = Constraints()
            pos = PositionConstraint(); pos.header.frame_id = "base_link"; pos.link_name = "tool0"; pos.weight = 1.0
            box = BoundingVolume(); prim = SolidPrimitive(); prim.type = SolidPrimitive.BOX; prim.dimensions = [0.01, 0.01, 0.01]
            box.primitives.append(prim); p = PoseStamped(); p.header.frame_id = "base_link"; p.pose = pose
            box.primitive_poses.append(p.pose); pos.constraint_region = box; constraints.position_constraints.append(pos)
            ori = OrientationConstraint(); ori.header.frame_id = "base_link"; ori.link_name = "tool0"; ori.orientation = pose.orientation 
            ori.absolute_x_axis_tolerance = 0.5; ori.absolute_y_axis_tolerance = 0.5; ori.absolute_z_axis_tolerance = 3.14; ori.weight = 1.0
            constraints.orientation_constraints.append(ori); goal.request.goal_constraints.append(constraints)
            self._move_group_client.wait_for_server(); self._move_group_client.send_goal_async(goal)
            return True
        except: return False

    def move_smart(self, target_pose):
        try:
            req = GetCartesianPath.Request(); req.header.frame_id = "base_link"; req.group_name = "ur_manipulator"
            req.start_state = RobotState(); req.start_state.joint_state = self.joint_state
            req.waypoints = [target_pose]; req.max_step = 0.01; req.jump_threshold = 0.0; req.avoid_collisions = True
            future = self._cartesian_client.call_async(req)
            # Wir warten kurz, ob Cartesian klappt
            start = time.time()
            while not future.done():
                time.sleep(0.01)
                if time.time() - start > 0.5: break # Timeout
            
            if future.done():
                res = future.result()
                if res and res.fraction > 0.90: return self.execute_trajectory(res.solution)
        except: pass
        return self.move_ptp(target_pose)

    def move_linear_relative(self, dz):
        # FK Abfrage (vereinfacht für Thread-Safety)
        req = GetPositionFK.Request(); req.header.frame_id = "base_link"; req.fk_link_names = ["tool0"]; req.robot_state.joint_state = self.joint_state
        future = self._fk_client.call_async(req)
        while not future.done(): time.sleep(0.01)
        
        if future.result():
            start = future.result().pose_stamped[0].pose
            target = copy.deepcopy(start); target.position.z += dz
            
            req = GetCartesianPath.Request(); req.header.frame_id = "base_link"; req.group_name = "ur_manipulator"
            req.start_state = RobotState(); req.start_state.joint_state = self.joint_state
            req.waypoints = [target]; req.max_step = 0.01; req.jump_threshold = 0.0
            future = self._cartesian_client.call_async(req)
            while not future.done(): time.sleep(0.01)
            
            res = future.result()
            if res.fraction > 0.9: self.execute_trajectory(res.solution); return True
        return False

    def execute_pick_cycle(self):
        print(f"✊ Greife! Fahre {HOVER_DISTANCE}m runter...")
        if self.move_linear_relative(-HOVER_DISTANCE):
            time.sleep(0.5)
            print("🛫 Fahre wieder hoch...")
            self.move_linear_relative(HOVER_DISTANCE)

    def trigger_move_to(self, pose_data):
        p = Pose()
        p.position.x = pose_data['x']
        p.position.y = pose_data['y']
        p.position.z = pose_data['z'] + HOVER_DISTANCE
        p.orientation = pose_data['ori']
        
        # --- FIX: Target für RViz setzen ---
        ps = PoseStamped()
        ps.header.frame_id = "base_link"
        ps.header.stamp = self.get_clock().now().to_msg()
        # WICHTIG: Wir zeigen in RViz das *Objekt* an (ohne Hover Distance),
        # damit der Pfeil auf dem Bauteil liegt (wie im alten Code)
        p_viz = copy.deepcopy(p)
        p_viz.position.z = pose_data['z'] 
        ps.pose = p_viz
        
        self.current_target_msg = ps # Ab jetzt publisht der Timer diesen Pfeil
        # -----------------------------------
        
        print(f"Bewege zu: {p.position.x}, {p.position.y}")
        self.move_smart(p)

    def go_home(self):
        self.current_target_msg = None # Pfeil löschen bei Reset
        goal = MoveGroup.Goal(); goal.request.workspace_parameters.header.frame_id = "base_link"; goal.request.group_name = "ur_manipulator"
        target_joints = [0.0, -1.57, 1.57, -1.57, -1.57, 0.0]
        constraints = Constraints()
        names = ["shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"]
        for i, n in enumerate(names):
            jc = JointConstraint(); jc.joint_name = n; jc.position = target_joints[i]; jc.tolerance_above = 0.01; jc.tolerance_below = 0.01; jc.weight = 1.0
            constraints.joint_constraints.append(jc)
        goal.request.goal_constraints.append(constraints)
        self._move_group_client.wait_for_server()
        self._move_group_client.send_goal_async(goal)