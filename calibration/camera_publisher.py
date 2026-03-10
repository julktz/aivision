#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
import cv2
import math
from std_msgs.msg import Header
from geometry_msgs.msg import PoseStamped, Quaternion, TransformStamped
from sensor_msgs.msg import Image, JointState, CameraInfo, PointCloud2, PointField
from cv_bridge import CvBridge
from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster

IMG_WIDTH = 640
IMG_HEIGHT = 480
CAMERA_Z = 0.50
FOV_WIDTH_M = 0.55

class CalibrationCamera(Node):
    def __init__(self):
        super().__init__('industrial_robot_node')
        self.get_logger().info("🔵 Starte Fast Calibration Vision (NO YOLO, Async Timer)...")

        self.sub_joints = self.create_subscription(JointState, '/joint_states', self.joint_cb, 10)
        self.img_pub = self.create_publisher(Image, '/video_frames', 10)
        self.cam_info_pub = self.create_publisher(CameraInfo, '/camera_info', 10)
        self.cloud_pub = self.create_publisher(PointCloud2, '/video_cloud', 10)

        self.tf_static_broadcaster = StaticTransformBroadcaster(self)
        self.publish_static_tf()

        self.cap = cv2.VideoCapture(2)
        
        self.meters_per_pixel = FOV_WIDTH_M / IMG_WIDTH
        self.focal_length = (IMG_WIDTH * CAMERA_Z) / FOV_WIDTH_M
        
        self.joint_state = None
        self.bridge = CvBridge()
        
        # ROS 2 holt sich das Bild jetzt asynchron alle ~33ms (30 FPS)
        self.timer = self.create_timer(1.0 / 30.0, self.timer_callback)
        cv2.namedWindow("Calibration Fast View")

    def euler_to_quaternion(self, r, p, y):
        r, p, y = math.radians(r), math.radians(p), math.radians(y)
        cr, sr = math.cos(r * 0.5), math.sin(r * 0.5)
        cp, sp = math.cos(p * 0.5), math.sin(p * 0.5)
        cy, sy = math.cos(y * 0.5), math.sin(y * 0.5)
        q = Quaternion()
        q.w = cr * cp * cy + sr * sp * sy
        q.x = sr * cp * cy - cr * sp * sy
        q.y = cr * sp * cy + sr * cp * sy
        q.z = cr * cp * sy - sr * sp * cy
        return q

    def joint_cb(self, msg):
        self.joint_state = msg

    def publish_static_tf(self):
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = 'base_link'
        t.child_frame_id = 'camera_optical_frame'
        t.transform.translation.x = 0.5
        t.transform.translation.y = 0.0
        t.transform.translation.z = 1.0
        q = self.euler_to_quaternion(180, 0, 0)
        t.transform.rotation = q
        self.tf_static_broadcaster.sendTransform(t)

    def publish_camera_data(self, frame, stamp):
        img_msg = self.bridge.cv2_to_imgmsg(frame, encoding="bgr8")
        img_msg.header.stamp = stamp
        img_msg.header.frame_id = "camera_optical_frame"
        self.img_pub.publish(img_msg)

        ci = CameraInfo()
        ci.header.stamp = stamp
        ci.header.frame_id = "camera_optical_frame"
        ci.width = IMG_WIDTH
        ci.height = IMG_HEIGHT
        ci.k = [662.75279, 0.0, 330.88457, 0.0, 664.99798, 251.53493, 0.0, 0.0, 1.0]
        ci.p = [665.83468, 0.0, 329.48278, 0.0, 0.0, 669.42326, 252.11935, 0.0, 0.0, 0.0, 1.0, 0.0]
        ci.distortion_model = "plumb_bob"
        ci.d = [0.069043, -0.218293, 0.001793, -0.003378, 0.000000]
        self.cam_info_pub.publish(ci)

    def create_point_cloud(self, frame, stamp):
        h, w = frame.shape[:2]
        fx = fy = self.focal_length
        cx, cy = w/2, h/2

        points = []
        for v in range(0, h, 15):
            for u in range(0, w, 15):
                x = (u - cx) * CAMERA_Z / fx
                y = (v - cy) * CAMERA_Z / fy
                points.append((x, y, CAMERA_Z))

        msg = PointCloud2()
        msg.header.stamp = stamp
        msg.header.frame_id = "camera_optical_frame"
        msg.height = 1
        msg.width = len(points)
        msg.fields = [
            PointField(name='x', offset=0, datatype=PointField.FLOAT32, count=1),
            PointField(name='y', offset=4, datatype=PointField.FLOAT32, count=1),
            PointField(name='z', offset=8, datatype=PointField.FLOAT32, count=1),
        ]
        msg.is_bigendian = False
        msg.point_step = 12
        msg.row_step = msg.point_step * msg.width
        msg.is_dense = True
        import struct
        msg.data = bytearray(struct.pack('%sf' % (len(points)*3), *[val for pt in points for val in pt]))
        self.cloud_pub.publish(msg)

    def timer_callback(self):
        ret, frame = self.cap.read()
        if not ret:
            return

        stamp = self.get_clock().now().to_msg()
        self.publish_camera_data(frame, stamp)
        self.create_point_cloud(frame, stamp)

        cv2.putText(frame, "KALIBRIERUNG (Keine KI - Async)", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.imshow("Calibration Fast View", frame)
        cv2.waitKey(1)

def main(args=None):
    rclpy.init(args=args)
    node = CalibrationCamera()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.cap.release()
        cv2.destroyAllWindows()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()
