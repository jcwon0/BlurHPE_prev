import copy
import os.path as osp
import tempfile

import mmcv
import numpy as np
import pytest

from mmpose.datasets.pipelines import Compose

H36M_JOINT_IDX = [14, 2, 1, 0, 3, 4, 5, 16, 12, 17, 18, 9, 10, 11, 8, 7, 6]


def get_data_sample():

    def _parse_h36m_imgname(imgname):
        """Parse imgname to get information of subject, action and camera.

        A typical h36m image filename is like:
        S1_Directions_1.54138969_000001.jpg
        """
        subj, rest = osp.basename(imgname).split('_', 1)
        action, rest = rest.split('.', 1)
        camera, rest = rest.split('_', 1)
        return subj, action, camera

    ann_flle = 'tests/data/h36m/test_h36m.npz'
    camera_param_file = 'tests/data/h36m/cameras.pkl'

    data = np.load(ann_flle)
    cameras = mmcv.load(camera_param_file)

    _imgnames = data['imgname']
    _joints_2d = data['part'][:, H36M_JOINT_IDX].astype(np.float32)
    _joints_3d = data['S'][:, H36M_JOINT_IDX].astype(np.float32)
    _centers = data['center'].astype(np.float32)
    _scales = data['scale'].astype(np.float32)

    frame_ids = [0]
    target_frame_id = 0

    results = {
        'frame_ids': frame_ids,
        'target_frame_id': target_frame_id,
        'input_2d': _joints_2d[frame_ids, :, :2],
        'input_2d_visible': _joints_2d[frame_ids, :, -1:],
        'input_3d': _joints_3d[frame_ids, :, :3],
        'input_3d_visible': _joints_3d[frame_ids, :, -1:],
        'target': _joints_3d[target_frame_id, :, :3],
        'target_visible': _joints_3d[target_frame_id, :, -1:],
        'imgnames': _imgnames[frame_ids],
        'scales': _scales[frame_ids],
        'centers': _centers[frame_ids],
    }

    # add camera parameters
    subj, _, camera = _parse_h36m_imgname(_imgnames[frame_ids[0]])
    results['camera_param'] = cameras[(subj, camera)]

    # add ann_info
    ann_info = {}
    ann_info['num_joints'] = 17
    ann_info['joint_weights'] = np.full(17, 1.0, dtype=np.float32)
    ann_info['flip_pairs'] = [[1, 4], [2, 5], [3, 6], [11, 14], [12, 15],
                              [13, 16]]
    ann_info['upper_body_ids'] = (0, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16)
    ann_info['lower_body_ids'] = (1, 2, 3, 4, 5, 6)
    ann_info['use_different_joint_weights'] = False

    results['ann_info'] = ann_info

    return results


def test_joint_transforms():
    results = get_data_sample()

    mean = np.random.rand(16, 3).astype(np.float32)
    std = np.random.rand(16, 3).astype(np.float32) + 1e-6

    pipeline = [
        dict(
            type='RelativeJointRandomFlip',
            item='target',
            root_index=0,
            visible_item='target_visible',
            flip_prob=1.),
        dict(
            type='JointRelativization',
            item='target',
            root_index=0,
            root_name='global_position',
            remove_root=True),
        dict(type='JointNormalization', item='target', mean=mean, std=std),
        dict(type='PoseSequenceToTensor', item='target'),
        dict(
            type='Collect',
            keys=[('target', 'output'), 'flip_pairs'],
            meta_keys=[])
    ]

    pipeline = Compose(pipeline)
    output = pipeline(copy.deepcopy(results))

    joints_0 = results['target']
    joints_1 = output['output'].numpy()

    # manually do transformations
    flip_pairs = output['flip_pairs']
    _joints_0_flipped = joints_0.copy()
    for _l, _r in flip_pairs:
        _joints_0_flipped[..., _l, :] = joints_0[..., _r, :]
        _joints_0_flipped[..., _r, :] = joints_0[..., _l, :]

    _joints_0_flipped[...,
                      0] = 2 * joints_0[..., 0:1, 0] - _joints_0_flipped[...,
                                                                         0]

    joints_0 = _joints_0_flipped
    joints_0 = (joints_0[..., 1:, :] - joints_0[..., 0:1, :] - mean) / std

    # convert to [K*C, T]
    joints_0 = joints_0.reshape(-1)[..., None]

    np.testing.assert_array_almost_equal(joints_0, joints_1)

    # test load mean/std from file
    with tempfile.TemporaryDirectory() as tmpdir:
        norm_param = {'mean': mean, 'std': std}
        norm_param_file = osp.join(tmpdir, 'norm_param.pkl')
        mmcv.dump(norm_param, norm_param_file)

        pipeline = [
            dict(
                type='JointNormalization',
                item='target',
                norm_param_file=norm_param_file),
        ]
        pipeline = Compose(pipeline)


def test_camera_projection():
    results = get_data_sample()
    pipeline_1 = [
        dict(
            type='CameraProjection',
            item='input_3d',
            output_name='input_3d_w',
            camera_type='SimpleCamera',
            mode='camera_to_world'),
        dict(
            type='CameraProjection',
            item='input_3d_w',
            output_name='input_3d_wp',
            camera_type='SimpleCamera',
            mode='world_to_pixel'),
        dict(
            type='CameraProjection',
            item='input_3d',
            output_name='input_3d_p',
            camera_type='SimpleCamera',
            mode='camera_to_pixel'),
        dict(type='Collect', keys=['input_3d_wp', 'input_3d_p'], meta_keys=[])
    ]
    camera_param = results['camera_param'].copy()
    camera_param['K'] = np.concatenate(
        (np.diagflat(camera_param['f']), camera_param['c']), axis=-1)
    pipeline_2 = [
        dict(
            type='CameraProjection',
            item='input_3d',
            output_name='input_3d_w',
            camera_type='SimpleCamera',
            camera_param=camera_param,
            mode='camera_to_world'),
        dict(
            type='CameraProjection',
            item='input_3d_w',
            output_name='input_3d_wp',
            camera_type='SimpleCamera',
            camera_param=camera_param,
            mode='world_to_pixel'),
        dict(
            type='CameraProjection',
            item='input_3d',
            output_name='input_3d_p',
            camera_type='SimpleCamera',
            camera_param=camera_param,
            mode='camera_to_pixel'),
        dict(
            type='CameraProjection',
            item='input_3d_w',
            output_name='input_3d_wc',
            camera_type='SimpleCamera',
            camera_param=camera_param,
            mode='world_to_camera'),
        dict(type='Collect', keys=['input_3d_wp', 'input_3d_p'], meta_keys=[])
    ]

    output1 = Compose(pipeline_1)(results)
    output2 = Compose(pipeline_2)(results)

    np.testing.assert_allclose(
        output1['input_3d_wp'], output1['input_3d_p'], rtol=1e-6)

    np.testing.assert_allclose(
        output2['input_3d_wp'], output2['input_3d_p'], rtol=1e-6)

    # test invalid camera parameters
    with pytest.raises(ValueError):
        # missing intrinsic parameters
        camera_param_wo_intrinsic = camera_param.copy()
        camera_param_wo_intrinsic.pop('K')
        camera_param_wo_intrinsic.pop('f')
        camera_param_wo_intrinsic.pop('c')
        _ = Compose([
            dict(
                type='CameraProjection',
                item='input_3d',
                camera_type='SimpleCamera',
                camera_param=camera_param_wo_intrinsic,
                mode='camera_to_pixel')
        ])

    with pytest.raises(ValueError):
        # invalid mode
        _ = Compose([
            dict(
                type='CameraProjection',
                item='input_3d',
                camera_type='SimpleCamera',
                camera_param=camera_param,
                mode='dummy')
        ])

    # test camera without undistortion
    camera_param_wo_undistortion = camera_param.copy()
    camera_param_wo_undistortion.pop('k')
    camera_param_wo_undistortion.pop('p')
    _ = Compose([
        dict(
            type='CameraProjection',
            item='input_3d',
            camera_type='SimpleCamera',
            camera_param=camera_param_wo_undistortion,
            mode='camera_to_pixel')
    ])
