import glob
import os
import pickle
import warnings

import matplotlib.pyplot as plt
from scipy.optimize import least_squares

from . import skeleton
from .Camera import Camera
from .LegBP import LegBP
from .bundle_adjustment import *
from .cv_util import *


class CameraNetwork:
    def __init__(self, image_folder, calibration=None, num_images=900, num_joints=38, image_shape=(480, 960),
                 heatmap_shape=(64, 128), cam_id_list=(0, 1, 2), cid2cidread=None, heatmap=None, pred=None,
                 cam_list=None, hm_path=None, pred_path=None):
        self.folder = image_folder
        self.dict_name = image_folder
        self.points3d_m = None
        self.mask_unique = None
        self.mask_prior = None
        self.bone_param = None

        self.num_images = num_images
        self.num_joints = num_joints
        self.heatmap_shape = heatmap_shape
        self.image_shape = image_shape
        self.num_cameras = len(cam_id_list)

        self.cam_list = list() if cam_list is None else cam_list

        self.cid2cidread = cid2cidread if cid2cidread is not None else cam_id_list

        if not cam_list:
            if pred_path is None:
                pred_path_list = glob.glob(os.path.join(self.folder, 'pred*.pkl'))
                pred_path_list.sort(key=os.path.getmtime)
                pred_path_list = pred_path_list[::-1]
            else:
                pred_path_list = [pred_path]
            print("Loading predictions {}".format(pred_path_list))
            if pred is None and len(pred_path_list) != 0:
                pred = np.load(pred_path_list[0], mmap_mode='r')
                num_images_in_pred = pred.shape[1]

            if type(pred) == dict:
                pred = None

            if hm_path is None:
                heatmap_path_list = glob.glob(os.path.join(self.folder, 'heatmap*.pkl'))
                heatmap_path_list.sort(key=os.path.getmtime)
                heatmap_path_list = heatmap_path_list[::-1]
            else:
                heatmap_path_list = [hm_path]
            print("Loading heatmaps {}".format(heatmap_path_list))
            if heatmap is None and len(heatmap_path_list) and pred is not None:
                try:
                    shape = (
                        skeleton.num_cameras + 1, num_images_in_pred, skeleton.num_joints // 2, self.heatmap_shape[0],
                        self.heatmap_shape[1])
                    print('Heatmap shape: {}'.format(shape))
                    heatmap = np.memmap(filename=heatmap_path_list[0], mode='r',
                                        shape=shape, dtype='float32')
                except BaseException as e:
                    print("Cannot read heatmap as memory mapped: {}".format(heatmap_path_list))
                    heatmap = np.load(heatmap_path_list[0])
                    self.dict_name = os.path.dirname(list(heatmap.keys())[10]) + '/'

            for cam_id, cam_id_read in zip(cam_id_list, self.cid2cidread):
                if heatmap is not None and type(heatmap) is np.core.memmap:
                    #heatmap_cam = np.zeros(shape=(num_images_in_pred, num_joints, heatmap_shape[0], heatmap_shape[1]),
                    #                       dtype=float)
                    pred_cam = np.zeros(shape=(num_images_in_pred, num_joints, 2), dtype=float)
                    if cam_id > 3:
                        #heatmap_cam[:num_images_in_pred, num_joints // 2:, :] = heatmap[cam_id_read,
                        #                                                        :num_images_in_pred]
                        pred_cam[:num_images_in_pred, num_joints // 2:, :] = pred[cam_id_read, :num_images_in_pred] * [
                            960, 480]
                    elif cam_id == 3:
                        #heatmap_cam[:num_images_in_pred, :num_joints // 2, :] = heatmap[cam_id_read,
                        #                                                        :num_images_in_pred]
                        pred_cam[:num_images_in_pred, :num_joints // 2, :] = pred[cam_id_read, :num_images_in_pred] * [
                            960, 480]
                        cam_id_mirror = 7
                        #heatmap_cam[:num_images_in_pred, num_joints // 2:, :] = heatmap[cam_id_mirror,
                        #                                                        :num_images_in_pred]
                        pred_cam[:num_images_in_pred, num_joints // 2:, :] = pred[cam_id_mirror,
                                                                             :num_images_in_pred] * [960,
                                                                                                     480]
                    elif cam_id < 3:
                        #heatmap_cam[:num_images_in_pred, :num_joints // 2, :] = heatmap[cam_id_read,
                        #                                                        :num_images_in_pred]
                        pred_cam[:num_images_in_pred, :num_joints // 2, :] = pred[cam_id_read, :num_images_in_pred] * [
                            960, 480]
                    else:
                        raise NotImplementedError
                else:
                    print("Skipping reading heatmaps and predictions")
                    heatmap = np.zeros(shape=(skeleton.num_cameras+1, num_images, num_joints, heatmap_shape[0], heatmap_shape[1]), dtype=float)
                    pred_cam = np.zeros(shape=(num_images, num_joints, 2), dtype=float)
                self.cam_list.append(
                    Camera(cid=cam_id, cid_read=cam_id_read, image_folder=image_folder, json_path=None, hm=heatmap,
                           points2d=pred_cam))

            '''
            for cam in self.cam_list:
                if cam.points2d is None and cam.hm is not None:
                    warnings.warn("Calculating Camera {} 2d points from heatmaps".format(cam.cam_id))
                    cam.points2d = Camera.hm_to_pred(cam.hm, scale=(960, 480), threshold_abs=0.0, num_pred=1)
            '''

        if calibration is not None:
            _ = self.load_network(calibration)

        '''
        if not np.all(self.cam_list[0].hm == 0):
            self.calc_mask_prior()
        '''

    def set_cid2cidread(self, cid2cidread):
        assert (len(self.cam_list) == len(cid2cidread))
        self.cid2cidread = cid2cidread
        for cam, cidread in zip(self.cam_list, cid2cidread):
            cam.cam_id_read = cidread

    def __getitem__(self, key):
        return self.cam_list[key]

    def __iter__(self):
        return iter(self.cam_list)

    def has_calibration(self):
        return np.all([c.R is not None for c in self])

    def has_pose(self):
        return self.has_heatmap()

    def has_heatmap(self):
        return self[0].hm is not None

    def calc_mask_prior(self, thr=50):
        self.mask_prior = np.zeros(self[0].points2d.shape, dtype=bool)
        for (img_id, joint_id, _), _ in np.ndenumerate(self.mask_prior):
            l = [np.abs(cam[img_id, joint_id][0][1]) for cam in self.cam_list if
                 skeleton.camera_see_joint(cam.cam_id, joint_id)]

            is_aligned = len(l) and ((np.max(l) - np.min(l)) < thr)
            self.mask_prior[img_id, joint_id, :] = is_aligned

        print("Number of points close to prior epipolar line: {}".format(np.sum(self.mask_prior) / 2))

    def triangulate(self, cam_indices=None):
        if cam_indices is None:
            cam_indices = list(range(self.num_cameras))
        points2d_shape = self[0].points2d.shape
        self.points3d_m = np.zeros(shape=(points2d_shape[0], points2d_shape[1], 3), dtype=np.float)
        data_shape = self.cam_list[0].points2d.shape
        for img_id in range(data_shape[0]):
            for j_id in range(data_shape[1]):
                cam_list_iter = list()
                points2d_iter = list()
                for cam in [self.cam_list[cam_idx] for cam_idx in cam_indices]:
                    if np.any(cam[img_id, j_id, :] == 0):
                        continue
                    if not skeleton.camera_see_joint(cam.cam_id, j_id):
                        continue
                    cam_list_iter.append(cam)
                    points2d_iter.append(cam[img_id, j_id, :])

                if len(cam_list_iter) >= 2:
                    self.points3d_m[img_id, j_id, :] = triangulate_linear(cam_list_iter, points2d_iter)

    def calc_mask_unique(self):
        # mask on points2d where observations are present and unique
        for cam in self.cam_list:
            if cam.mask_unique is None:
                cam.calc_mask_unique()

        self.mask_unique = np.logical_and.reduce([cam.mask_unique for cam in self.cam_list])

    def solvePnp(self, cam_id, ignore_joint_list=skeleton.ignore_joint_id):
        points3d_pnp = []
        points2d_pnp = []
        data_shape = self.cam_list[0].points2d.shape
        for img_id in range(data_shape[0]):
            for j_id in range(data_shape[1]):
                if not skeleton.camera_see_joint(cam_id, j_id):  # if the new camera sees the point
                    continue
                if np.any(self.cam_list[cam_id][img_id, j_id, :] == 0):  # if the point is present
                    continue
                if j_id in ignore_joint_list:  # if the joint is not ignored
                    continue
                if np.any(self.points3d_m[img_id, j_id] == 0):
                    continue
                points3d_pnp.append(self.points3d_m[img_id, j_id, :])
                points2d_pnp.append(self.cam_list[cam_id][img_id][img_id, j_id, :])

        objectPoints = np.array(points3d_pnp)
        imagePoints = np.array(points2d_pnp)

        print("objectPoints shape: {}".format(objectPoints.shape))
        if objectPoints.shape[0] > 4:
            found, rvec, tvec, mask = cv2.solvePnPRansac(
                objectPoints, imagePoints, self.cam_list[cam_id].intr, self.cam_list[cam_id].distort,
                useExtrinsicGuess=False)
            R = cv2.Rodrigues(rvec)[0]
            self.cam_list[cam_id].set_R(R)
            self.cam_list[cam_id].set_tvec(tvec)
        else:
            print("Skipping PnP, not enough points")

    def reprojection_error(self, cam_indices=None, ignore_joint_list=None):
        if ignore_joint_list is None:
            ignore_joint_list = skeleton.ignore_joint_id
        if cam_indices is None:
            cam_indices = range(len(self.cam_list))

        err_list = list()
        for (img_id, j_id, _), _ in np.ndenumerate(self.points3d_m):
            p3d = self.points3d_m[img_id, j_id].reshape(1, 3)
            if j_id in ignore_joint_list:
                continue
            for cam in self.cam_list:
                if not skeleton.camera_see_joint(cam.cam_id, j_id):
                    continue
                err_list.append((cam.project(p3d) - cam[img_id, j_id]).ravel())

        err_mean = np.mean(np.abs(err_list))
        print("Ignore_list {}:  {:.4f}".format(ignore_joint_list, err_mean))
        return err_list

    def prepare_bundle_adjust_param(self, camera_id_list=None, ignore_joint_list=None, unique=False, prior=True):
        if ignore_joint_list is None:
            ignore_joint_list = skeleton.ignore_joint_id
        if camera_id_list is None:
            camera_id_list = list(range(self.num_cameras))

        camera_params = np.zeros(shape=(len(camera_id_list), 13), dtype=float)
        cam_list = [self.cam_list[c] for c in camera_id_list]
        for i, cid in enumerate(camera_id_list):
            camera_params[i, 0:3] = np.squeeze(cam_list[cid].rvec)
            camera_params[i, 3:6] = np.squeeze(cam_list[cid].tvec)
            camera_params[i, 6] = cam_list[cid].focal_length_x
            camera_params[i, 7] = cam_list[cid].focal_length_y
            camera_params[i, 8:13] = np.squeeze(cam_list[cid].distort)

        point_indices = []
        camera_indices = []
        points2d_ba = []
        points3d_ba = []
        points3d_ba_source = dict()
        points3d_ba_source_inv = dict()
        point_index_counter = 0
        data_shape = self.points3d_m.shape
        for img_id in range(data_shape[0]):
            for j_id in range(data_shape[1]):
                cam_list_iter = list()
                points2d_iter = list()
                for cam in cam_list:
                    if j_id in ignore_joint_list:
                        continue
                    if np.any(self.points3d_m[img_id, j_id, :] == 0):
                        continue
                    if np.any(cam[img_id, j_id, :] == 0):
                        continue
                    if not skeleton.camera_see_joint(cam.cam_id, j_id):
                        continue
                    #if prior and not self.mask_prior[img_id, j_id, 0]:
                    #    continue
                    if unique and not self.mask_unique[img_id, j_id, 0]:
                        continue

                    cam_list_iter.append(cam)
                    points2d_iter.append(cam[img_id, j_id, :])

                # the point is seen by at least two cameras, add it to the bundle adjustment
                if len(cam_list_iter) >= 2:
                    points3d_iter = self.points3d_m[img_id, j_id, :]
                    points2d_ba.extend(points2d_iter)
                    points3d_ba.append(points3d_iter)
                    point_indices.extend([point_index_counter] * len(cam_list_iter))
                    points3d_ba_source[(img_id, j_id)] = point_index_counter
                    points3d_ba_source_inv[point_index_counter] = (img_id, j_id)
                    point_index_counter += 1
                    camera_indices.extend([cam.cam_id for cam in cam_list_iter])

        c = 0
        # make sure stripes from both sides share the same point id's
        for idx, point_idx in enumerate(point_indices):
            img_id, j_id = points3d_ba_source_inv[point_idx]
            if skeleton.tracked_points[j_id] == skeleton.Tracked.STRIPE and j_id > skeleton.num_joints // 2:
                if (img_id, j_id - skeleton.num_joints // 2) in points3d_ba_source:
                    point_indices[idx] = points3d_ba_source[(img_id, j_id - skeleton.num_joints // 2)]
                    c += 1

        print('Replaced {} points'.format(c))
        points3d_ba = np.squeeze(np.array(points3d_ba))
        points2d_ba = np.squeeze(np.array(points2d_ba))
        camera_indices = np.array(camera_indices)
        point_indices = np.array(point_indices)

        camera_indices -= np.min(camera_indices)  # XXX assumes cameras are consecutive :/

        n_cameras = camera_params.shape[0]
        n_points = points3d_ba.shape[0]

        x0 = np.hstack((camera_params.ravel(), points3d_ba.ravel()))

        return x0.copy(), points2d_ba.copy(), n_cameras, n_points, camera_indices, point_indices

    def bundle_adjust(self, cameras_involved=None, ignore_joint_list=skeleton.ignore_joint_id,
                      unique=True,
                      prior=False):
        # TODO iteratively remove high residual points
        # TODO huber loss
        if cameras_involved is None:
            cameras_involved = range(self.num_cameras)

        self.reprojection_error(cam_indices=(0, 1, 2), ignore_joint_list=ignore_joint_list)
        x0, points_2d, n_cameras, n_points, camera_indices, point_indices = self.prepare_bundle_adjust_param(
            cameras_involved, ignore_joint_list=ignore_joint_list, unique=unique, prior=prior)

        print("Number of points: ", n_points)
        A = bundle_adjustment_sparsity(n_cameras, n_points, camera_indices, point_indices)
        res = least_squares(fun, x0, jac_sparsity=A, verbose=2, x_scale='jac', ftol=1e-4, method='trf',
                            args=(
                                [self.cam_list[i] for i in cameras_involved], n_cameras, n_points, camera_indices,
                                point_indices,
                                points_2d), max_nfev=1000)
        '''
        q = 99.0
        thr = np.percentile(res.fun, q)
        print("Residual quantile {}th: {}".format(q, thr))
        residual_mask = np.ones_like(res.fun)
        residual_mask[res.fun>thr] = 0
        res = least_squares(fun, x0, jac_sparsity=A, verbose=2, x_scale='jac', ftol=1e-4, method='trf',
                        args=(
                            [self.cam_list[i] for i in cameras_involved], n_cameras, n_points, camera_indices,
                            point_indices,
                            points_2d, residual_mask), max_nfev=1000)
        '''

        print("Bundle adjustment, Average reprojection error: {}".format(np.mean(np.abs(res.fun))))

        self.triangulate()
        plt.plot(res.fun)

        return res

    def solveBP(self, img_id, bone_param, num_peak=10, prior=None):
        # find all the connected parts
        j_id_list_list = [[j for j in range(skeleton.num_joints) if skeleton.limb_id[j] == limb_id] for limb_id in
                          range(skeleton.num_limbs)]

        chain_list = list()
        for j_id_l in j_id_list_list:
            visible = np.zeros(shape=(len(j_id_l),), dtype=np.int)
            for cam in self.cam_list:
                visible += [skeleton.camera_see_joint(cam.cam_id, j_id) for j_id in j_id_l]
            if np.all(visible >= 2):
                chain_list.append(LegBP(camera_network=self, img_id=img_id, j_id_list=j_id_l, bone_param=bone_param,
                                        num_peak=num_peak, prior=prior))
            else:
                print("Joints {} is not visible from at least two cameras".format(j_id_l))

        print([[len(leg[i].candid_list) for i in range(len(leg.jointbp))] for leg in chain_list])
        for chain in chain_list:
            chain.propagate()
            chain.solve()

        # read the best 2d locations
        points2d_list = [np.zeros((skeleton.num_joints, 2), dtype=float) for _ in range(len(self.cam_list))]
        for leg in chain_list:
            for cam_idx in range(self.num_cameras):
                for idx, j_id in enumerate(leg.j_id_list):
                    points2d_list[cam_idx][j_id] = leg[idx][leg[idx].argmin].p2d[cam_idx]

        return points2d_list.copy()

    def save_network(self, path, meta=None):
        if path is not None and os.path.exists(path):  # to prevent overwriting
            d = pickle.load(open(path, 'rb'))
        else:
            d = {cam_id: dict() for cam_id in np.arange(0, 7)}
            d["meta"] = meta

        for cam in self:
            d[cam.cam_id]["R"] = cam.R
            d[cam.cam_id]["tvec"] = cam.tvec
            d[cam.cam_id]["intr"] = cam.intr
            d[cam.cam_id]["distort"] = cam.distort

        if path is not None:
            pickle.dump(d, open(path, 'wb'))
        else:
            return d

    def load_network(self, calib):
        d = calib
        for cam in self:
            if cam.cam_id in d and d[cam.cam_id]:
                cam.set_R(d[cam.cam_id]["R"])
                cam.set_tvec(d[cam.cam_id]["tvec"])
                cam.set_intrinsic(d[cam.cam_id]["intr"])
                cam.set_distort(d[cam.cam_id]["distort"])
            else:
                print("Camera {} is not on the calibration file".format(cam.cam_id))

        return d["meta"]

    '''
    STATIC
    '''

    @staticmethod
    def calc_essential_matrix(points2d_1, points2d_2, intr):
        E, mask = cv2.findEssentialMat(points1=points2d_1, points2=points2d_2, cameraMatrix=intr, method=cv2.RANSAC,
                                       prob=0.9999, threshold=5)
        print("Essential matrix inlier ratio: {}".format(np.sum(mask) / mask.shape[0]))
        return E, mask

    @staticmethod
    def calc_Rt_from_essential(E, points1, points2, intr):
        retval, R, t, mask, _ = cv2.recoverPose(E, points1=points1, points2=points2, cameraMatrix=intr,
                                                distanceThresh=100)
        return R, t, mask

    @staticmethod
    def plot_network(cam_list=None, circle=False):
        camera_tvec = np.array([c.tvec for c in cam_list])
        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        ax.set_aspect('equal')
        colors = ['red', 'green', 'blue', 'cyan', 'purple', 'gray']
        ax.set_xlim(-120, 120)
        ax.set_ylim(-120, 120)
        ax.set_zlim(-120, 120)

        X, Y, Z = camera_tvec[:, 0], camera_tvec[:, 1], camera_tvec[:, 2]

        # Plot the fly
        u = np.linspace(0, 2 * np.pi, 10)
        v = np.linspace(0, np.pi, 10)
        x = 10 * np.outer(np.cos(u), np.sin(v))
        y = 10 * np.outer(np.sin(u), np.sin(v))
        z = 10 * np.outer(np.ones(np.size(u)), np.cos(v))
        ax.plot_surface(x, y, z, color='red')

        if circle:
            u = np.linspace(0, 2 * np.pi, 10)
            v = np.linspace(0, np.pi, 10)
            x = 94 * np.outer(np.cos(u), np.sin(v))
            y = np.ones(x.shape)
            z = 94 * np.outer(np.ones(np.size(u)), np.cos(v))
            ax.plot_surface(x, y, z, color='b')

        # Plot the orientation
        for c in cam_list:
            start_points = np.repeat([-c.R.T.dot(c.tvec)], repeats=2, axis=0)
            dir = c.R.T.dot([0, 0, 10])
            start_points[1, :] = start_points[1, :] + dir
            ax.scatter(start_points[0, 0], start_points[0, 1], start_points[0, 2])
            ax.plot(start_points[:, 0], start_points[:, 1], start_points[:, 2])

        # Plot the cameras
        # ax.scatter(X,Y,Z,color=colors)
        ax.set_xlabel('X')
        ax.set_ylabel('Y')
        ax.set_zlabel('Z')

    '''
    PLOT
    '''

    '''
    def plot_drosoph(self, img_id, draw_joints=[2, 3, 4]):
        pose_overlay_list = []
        for cam in self.cam_list:
            pose_overlay_list.append(cam.plot_2d(img_id=img_id, draw_joints=draw_joints))
        pose_overlay_stack = np.hstack(pose_overlay_list)

        heatmap_overlay_list = []
        for cam in self.cam_list:
            heatmap_overlay_list.append(cam.plot_heatmap(img_id=img_id, concat=True, draw_joints=draw_joints))
        heatmap_overlay_stack = np.vstack(heatmap_overlay_list)

        heatmap_overlay_stack = scipy.misc.imresize(heatmap_overlay_stack,
                                                    pose_overlay_stack.shape[1] / heatmap_overlay_stack.shape[1])

        img = np.vstack([pose_overlay_stack, heatmap_overlay_stack])
        img = cv2.putText(img, "{}".format(img_id), (img.shape[1] - 200, 100), cv2.FONT_HERSHEY_PLAIN, 4, (255, 0, 0),
                          5, cv2.LINE_AA)
        return img

    def plot_2d(self, img_id, draw_joints=(2, 3, 4)):
        pose_overlay_list = []
        for cam in self.cam_list:
            pose_overlay_list.append(cam.plot_2d(img_id=img_id, draw_joints=draw_joints))
        img = np.hstack(pose_overlay_list)

        img = cv2.putText(img, "{}".format(img_id), (img.shape[1] - 200, 100), cv2.FONT_HERSHEY_PLAIN, 4, (255, 0, 0),
                          5, cv2.LINE_AA)
        return img
    '''