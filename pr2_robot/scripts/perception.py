#!/usr/bin/env python

# Import modules
import numpy as np
import sklearn
from sklearn.preprocessing import LabelEncoder
import pickle
from sensor_stick.srv import GetNormals
from sensor_stick.features import compute_color_histograms
from sensor_stick.features import compute_normal_histograms
from visualization_msgs.msg import Marker
from sensor_stick.marker_tools import *
from sensor_stick.msg import DetectedObjectsArray
from sensor_stick.msg import DetectedObject
from sensor_stick.pcl_helper import *

import rospy
import tf
from geometry_msgs.msg import Pose
from std_msgs.msg import Float64
from std_msgs.msg import Int32
from std_msgs.msg import String
from pr2_robot.srv import *
from rospy_message_converter import message_converter
import yaml


# Helper function to get surface normals
def get_normals(cloud):
    get_normals_prox = rospy.ServiceProxy('/feature_extractor/get_normals', GetNormals)
    return get_normals_prox(cloud).cluster

# Helper function to create a yaml friendly dictionary from ROS messages
def make_yaml_dict(test_scene_num, arm_name, object_name, pick_pose, place_pose):
    yaml_dict = {}
    yaml_dict["test_scene_num"] = test_scene_num.data
    yaml_dict["arm_name"]  = arm_name.data
    yaml_dict["object_name"] = object_name.data
    yaml_dict["pick_pose"] = message_converter.convert_ros_message_to_dictionary(pick_pose)
    yaml_dict["place_pose"] = message_converter.convert_ros_message_to_dictionary(place_pose)
    return yaml_dict

# Helper function to output to yaml file
def send_to_yaml(yaml_filename, dict_list):
    data_dict = {"object_list": dict_list}
    with open(yaml_filename, 'w') as outfile:
        yaml.dump(data_dict, outfile, default_flow_style=False)

# Callback function for your Point Cloud Subscriber
def pcl_callback(pcl_msg):

# Exercise-2 TODOs:

    # TODO: Convert ROS msg to PCL data
    pcl_cloud=ros_to_pcl(pcl_msg)
    
    # TODO: Statistical Outlier Filtering

    stat=pcl_cloud.make_statistical_outlier_filter()
    stat.set_mean_k(10)
    stat.set_std_dev_mul_thresh(0.1)
    stat_cloud=stat.filter()

    # TODO: Voxel Grid Downsampling
    vox = stat_cloud.make_voxel_grid_filter()
    leafSize = 0.01
    vox.set_leaf_size(leafSize,leafSize,leafSize)
    cloud_vox = vox.filter()

    # TODO: PassThrough Filter
    passthrough = cloud_vox.make_passthrough_filter()
    filter_axis = 'z'
    passthrough.set_filter_field_name(filter_axis)
    axis_min = 0.2
    axis_max = 10
    passthrough.set_filter_limits(axis_min,axis_max)
    temp_passthrough=passthrough.filter()
    passthrough2 = temp_passthrough.make_passthrough_filter()
    filter_axis = 'x'
    passthrough2.set_filter_field_name(filter_axis)
    axis_min = 0.4
    axis_max = 0.8
    passthrough2.set_filter_limits(axis_min,axis_max)
    cloud_passthrough = passthrough2.filter()
    #cloud_passthrough = passthrough.filter()

    # TODO: RANSAC Plane Segmentation
    segmented = cloud_passthrough.make_segmenter()
    segmented.set_model_type(pcl.SACMODEL_PLANE)
    segmented.set_method_type(pcl.SAC_RANSAC)
    threshold = 0.01
    segmented.set_distance_threshold(threshold)
    inliers, coefficients = segmented.segment()

    # TODO: Extract inliers and outliers
    cloud_table = cloud_passthrough.extract(inliers,negative = False)
    cloud_objects = cloud_passthrough.extract(inliers,negative = True)

    # TODO: Euclidean Clustering
    white_cloud = XYZRGB_to_XYZ(cloud_objects)
    tree = white_cloud.make_kdtree()

    # TODO: Create Cluster-Mask Point Cloud to visualize each cluster separately
    ec = white_cloud.make_EuclideanClusterExtraction()
    ec.set_ClusterTolerance(0.05)
    ec.set_MinClusterSize(50)
    ec.set_MaxClusterSize(800)

    ec.set_SearchMethod(tree)

    cluster_indices = ec.Extract()

    # Create colored cluster masks
    cluster_color = get_color_list(len(cluster_indices))

    color_cluster_point_list = []

    for j, indices in enumerate(cluster_indices):
    	for i, indice in enumerate(indices):
        	color_cluster_point_list.append([white_cloud[indice][0],
                	                         white_cloud[indice][1],
                        	                 white_cloud[indice][2],
                                	         rgb_to_float(cluster_color[j])])

    #Create new cloud containing all clusters, each with unique color
    cluster_cloud = pcl.PointCloud_PointXYZRGB()
    cluster_cloud.from_list(color_cluster_point_list)
    
    # TODO: Convert PCL data to ROS messages

    ros_cloud_table = pcl_to_ros(cloud_table)
    ros_cloud_objects = pcl_to_ros(cloud_objects)
    ros_cluster_cloud = pcl_to_ros(cluster_cloud)

    # TODO: Publish ROS messages

    pcl_objects_pub.publish(ros_cloud_objects)
    pcl_table_pub.publish(ros_cloud_table)
    pcl_cluster_pub.publish(ros_cluster_cloud)

# Exercise-3 TODOs:

    # Classify the clusters! (loop through each detected cluster one at a time)

    detected_objects_labels = []
    detected_objects = []

    for index,pts_list in enumerate(cluster_indices):
        # Grab the points for the cluster
        pcl_cluster = cloud_objects.extract(pts_list)
        ros_cluster = pcl_to_ros(pcl_cluster)

        # Compute the associated feature vector

        chists = compute_color_histograms(ros_cluster, using_hsv=True)
        normals = get_normals(ros_cluster)
        nhists = compute_normal_histograms(normals)
        feature = np.concatenate((chists, nhists))

        # Make the prediction

        prediction = clf.predict(scaler.transform(feature.reshape(1,-1)))
        label = encoder.inverse_transform(prediction)[0]
        detected_objects_labels.append(label)
        
        # Publish a label into RViz

        label_pos = list(white_cloud[pts_list[0]])
        label_pos[2] += .4
        object_markers_pub.publish(make_label(label,label_pos, index))

        # Add the detected object to the list of detected objects.

        do = DetectedObject()
        do.label = label
        do.cloud = ros_cluster
        detected_objects.append(do)

    # Publish the list of detected objects
    rospy.loginfo('Detected {} objects: {}'.format(len(detected_objects_labels), detected_objects_labels))
    detected_objects_pub.publish(detected_objects)

    # Suggested location for where to invoke your pr2_mover() function within pcl_callback()
    # Could add some logic to determine whether or not your object detections are robust
    # before calling pr2_mover()

    try:
        pr2_mover(detected_objects)
    except rospy.ROSInterruptException:
        pass

# function to load parameters and request PickPlace service
def pr2_mover(object_list):

    # TODO: Initialize variables
    object_list_param=[]
    labels=[]
    centroids=[]
    points_arr=[]
    dict_list=[]
    
    # TODO: Get/Read parameters
    object_list_param = rospy.get_param('/object_list')

    # TODO: Parse parameters into individual variables

    # TODO: Rotate PR2 in place to capture side tables for the collision map

    # TODO: Loop through the pick list
    for object in object_list_param:

    	#labels.append(object.label)
    	labels.append(object['name'])
        i=0
    	for i in range(len(object_list)):
    		if (object['name']==object_list[i].label):
    			break

    	test_scene_num=Int32()
        #test_scene_num.data=1
        #test_scene_num.data=2
        test_scene_num.data=3
        object_name=String()
        object_name.data=object['name']
        object_group=object['group']

        # TODO: Get the PointCloud for a given object and obtain it's centroid
    	points_arr=ros_to_pcl(object_list[i].cloud).to_array()
    	centroids.append(np.mean(points_arr,axis=0)[:3])

    	pick_pose=Pose()
    	pick_pose.position.x=np.asscalar(np.mean(points_arr,axis=0)[0])
    	pick_pose.position.y=np.asscalar(np.mean(points_arr,axis=0)[1])
    	pick_pose.position.z=np.asscalar(np.mean(points_arr,axis=0)[2])

        # TODO: Create 'place_pose' for the object
        place_pose_param=rospy.get_param('/dropbox')
        place_pose=Pose()
        j=0
        if (object_group=='green'):
        	j=1
        place_pose.position.x=place_pose_param[j]['position'][0]
        place_pose.position.y=place_pose_param[j]['position'][1]
        place_pose.position.z=place_pose_param[j]['position'][2]

        # TODO: Assign the arm to be used for pick_place

        arm_name=String()
        if (object_group=='green'):
        	arm_name.data='right'
        else:
        	arm_name.data='left'
        # TODO: Create a list of dictionaries (made with make_yaml_dict()) for later output to yaml format
        yaml_dict = make_yaml_dict(test_scene_num, arm_name, object_name, pick_pose, place_pose)
        dict_list.append(yaml_dict)
       
       	# Wait for 'pick_place_routine' service to come up
        rospy.wait_for_service('pick_place_routine')

        try:
            pick_place_routine = rospy.ServiceProxy('pick_place_routine', PickPlace)

            # TODO: Insert your message variables to be sent as a service request
            resp = pick_place_routine(test_scene_num, object_name, arm_name, pick_pose, place_pose)

            print ("Response: ",resp.success)

        except rospy.ServiceException, e:
            print "Service call failed: %s"%e

    # TODO: Output your request parameters into output yaml file
    send_to_yaml('output3.yaml',dict_list)


if __name__ == '__main__':

    # TODO: ROS node initialization
    rospy.init_node('clustering',anonymous = True)

    # TODO: Create Subscribers
    pcl_sub = rospy.Subscriber("/pr2/world/points",pc2.PointCloud2,pcl_callback,queue_size=1)

    # TODO: Create Publishers
    object_markers_pub=rospy.Publisher("/object_markers",Marker,queue_size=1)
    detected_objects_pub=rospy.Publisher("/detected_objects",DetectedObjectsArray,queue_size=1)
    pcl_objects_pub = rospy.Publisher("/pcl_objects", PointCloud2, queue_size=1)
    pcl_table_pub = rospy.Publisher("/pcl_table", PointCloud2, queue_size=1)
    pcl_cluster_pub = rospy.Publisher("/pcl_cluster", PointCloud2, queue_size=1)	

    
    # TODO: Load Model From disk
    model = pickle.load(open('model.sav', 'rb'))
    clf = model['classifier']
    encoder = LabelEncoder()
    encoder.classes_ = model['classes']
    scaler = model['scaler']
    

    get_color_list.color_list=[]
    # TODO: Spin while node is not shutdown
    while not rospy.is_shutdown():
    	rospy.spin()
