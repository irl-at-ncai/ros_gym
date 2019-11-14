#!/usr/bin/env python
import os
import subprocess
import rospy
from rospkg import RosPack
from mavros_msgs.msg import State, EstimatorStatus
from sensor_msgs.msg import NavSatFix
from mavros_msgs.srv import SetMode, CommandBool, CommandTOL
from geometry_msgs.msg import PoseStamped, TwistStamped

import ros_robot_env

class MavrosUAVRobotEnv(ros_robot_env.ROSRobotEnv):
    """Base class for all px4/mavros based uavs."""

    def __init__(self):
        rospy.loginfo('Setting up gazebo environment: MavrosUAVRobotEnv.')
        
        # list of controllers
        self.controllers_list = []
        
        # robot namespace
        self.robot_name_space = ''

        # launch connection to gazebo
        super(MavrosUAVRobotEnv, self).__init__()

        # set px4 pose estimator name
        est = rospy.get_param('mavros_gym/px4-est')
        if est == 'ekf2':
            self.pose_est_ = "px4-ekf2" 
        elif est == 'lpe':
            self.pose_est_ = "px4-local_position_estimator" 

    def _setup_subscribers(self):
        """
        Sets up all the subscribers relating to robot state
        """
        rospy.Subscriber('/mavros/state', State, callback=self._state_cb)
        rospy.Subscriber('/mavros/local_position/pose', PoseStamped, callback=self._pose_cb)
        rospy.Subscriber('/mavros/global_position/raw/fix', NavSatFix, callback=self._gps_cb)
        rospy.Subscriber('/mavros/estimator_status', EstimatorStatus, callback=self._est_status_cb)

    def _state_cb(self, msg):
        self._state = msg

    def _pose_cb(self, msg):
        self._pose = msg
    
    def _gps_cb(self, msg):
        self._gps = msg

    def _est_status_cb(self, msg):
        self._est_status = msg

    @property
    def state(self):
        return self._state

    @property
    def pose(self):
        return self._pose

    @property
    def gps(self):
        return self._gps
        
    def _check_all_subscribers_ready(self):
        """
        Checks that all the subscribers are ready for connection
        """
        self._state = self._check_subscriber_ready('/mavros/state', State)
        self._pose = self._check_subscriber_ready('/mavros/local_position/pose', PoseStamped)
        self._gps = self._check_subscriber_ready('/mavros/global_position/raw/fix', NavSatFix)
        self._est_status = self._check_subscriber_ready('/mavros/estimator_status', EstimatorStatus)

    def _check_all_publishers_ready(self):
        """
        Checks that all the publishers are ready for connection
        """
        self._check_publisher_ready(self._local_vel_pub.name, self._local_vel_pub)

    def _check_all_services_ready(self):
        """
        Checks that all the services are ready for connection
        """
        self._check_service_ready('/mavros/set_mode')
        self._check_service_ready('/mavros/cmd/arming')
        self._check_service_ready('/mavros/cmd/takeoff')
        self._check_service_ready('/mavros/cmd/land')

    def _setup_publishers(self):
        """
        Sets up all the publishers relating to robot state
        """
        # mavros publishers
        self._local_vel_pub = rospy.Publisher('mavros/setpoint_velocity/cmd_vel', TwistStamped, queue_size=1)

    def _pub_cmd_vel(self, vel_msg):
        self._local_vel_pub.publish(vel_msg)

    def _setup_services(self):
        # mavros services
        self._set_mode_client = rospy.ServiceProxy('/mavros/set_mode', SetMode)
        self._arming_client = rospy.ServiceProxy('/mavros/cmd/arming',CommandBool)
        self._takeoff_client = rospy.ServiceProxy('/mavros/cmd/takeoff', CommandTOL)
        self._land_client = rospy.ServiceProxy('/mavros/cmd/land', CommandTOL)

    def _set_service_request(self, name, cond, srv, req, wait_time=0.05, timeout=5.0):
        """Tries to set mavros px4 service requests until timeout is reached."""
        start_time = rospy.Time.now()
        if not cond():
            try:
                if srv(*req):
                    rospy.loginfo('Service %s request successful!', name)
                    while not cond() and not rospy.is_shutdown(): # wait for updated state
                        if (rospy.Time.now() - start_time).to_sec() >= timeout:
                            rospy.logfatal('Call to service %s successful but not response...', name)
                            return False
                    return True
                else:
                    rospy.logwarn('Call to service %s failed.', name)
                    return False
            except rospy.ServiceException as exc:
                rospy.logwarn('Call to service %s failed.', name)
                return False
        else:
            rospy.loginfo('Service %s condition is already true.', name)
            return True


    def _set_mode_request(self, mode, wait_time=0.05, timeout=5.0):
        """Sets the px4 flight mode using mavros service /mavros/set_mode"""
        return self._set_service_request(
            '/mavros/set_mode',
            lambda: self._state.mode == mode,
            self._set_mode_client,
            (0, mode), # 0 -> custom mode
            wait_time,
            timeout
        )

    def _set_arming_request(self, arm_req, wait_time=0.05, timeout=5.0):
        """Sets the px4 robot to arm/disarm using mavros service /mavros/cmd/arming"""
        return self._set_service_request(
            '/mavros/cmd/arming',
            lambda: self._state.armed == arm_req,
            self._arming_client,
            (arm_req,),
            wait_time,
            timeout
        )

    def _set_takeoff_request(self, takeoff_alt, wait_time=0.05, timeout=5.0):
        """Sets the px4 robot to arm/disarm using mavros service /mavros/cmd/arming"""
        return self._set_service_request(
            '/mavros/cmd/takeoff',
            lambda: self._state.mode == 'AUTO.TAKEOFF',
            self._takeoff_client,
            (0, 0, self._gps.latitude, self._gps.longitude, takeoff_alt),
            wait_time,
            timeout
        )

    def _set_land_request(self, land_alt, wait_time=0.05, timeout=5.0):
        """Sets the px4 robot to arm/disarm using mavros service /mavros/cmd/arming"""
        return self._set_service_request(
            '/mavros/cmd/land',
            lambda: self._state.mode == 'AUTO.LAND',
            self._land_client,
            (0, 0, self._gps.latitude, self._gps.longitude, land_alt),
            wait_time,
            timeout
        )

    def _reset_pose_estimator(self):
        self.px4_ekf2_path = os.path.join("/home/sai/ros-ws/melodic/lib/px4/" + self.pose_est_)
        time_stamp = self._est_status.header.stamp
        ekf2_stop = subprocess.Popen([self.px4_ekf2_path, "stop"])
        ekf2_stop.wait()
        ekf2_start = subprocess.Popen([self.px4_ekf2_path, "start"])
        ekf2_start.wait()
        while True:
            # wait for estimator to give a good estimate
            if self._est_status.header.stamp == time_stamp:
                continue

            if self._est_status.pred_hor_position_rel and self._est_status.pred_hor_position_abs:
                # ekf got valid states
                break
                
        #ekf2_status = subprocess.Popen([self.px4_ekf2_path, "status"])
        #ekf2_status.wait()