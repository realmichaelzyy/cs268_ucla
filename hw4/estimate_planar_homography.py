import pdb
import util_camera, util
import numpy as np, numpy.linalg, cv2

from util import intrnd
from util_camera import compute_x, compute_y, pt2homo, homo2pt

def estimate_planar_homography(I, line1, line2, K, win1, win2, lane_width):
    """ Estimates the planar homography H between the camera image
    plane, and the World (ground) plane.
    The World reference frame is directly below the camera, with:
        - Z-axis parallel to the road
        - X-axis on the road plane
        - Y-axis pointing upward from the road plane
        - origin is on the road plane (Y=0), and halfway between the
          two lanes boundaries (center of road).
    Input:
        nparray I
        nparray line1, line2: [a, b, c]
        nparray K
            The 3x3 camera intrinsic matrix.
        tuple win1, win2: (float x, float y, float w, float h)
        float lane_width
    Output:
        nparray H
    where H is a 3x3 homography.
    """
    h, w = I.shape[0:2]
    x_win1 = intrnd(w * win1[0])
    y_win1 = intrnd(h * win1[1])
    x_win2 = intrnd(w * win2[0])
    y_win2 = intrnd(h * win2[1])
    
    pts = []
    NUM = 10
    h_win = intrnd(h*win1[3]) # Assume window heights same btwn left/right
    for i in xrange(NUM):
        frac = i / float(NUM)
        y_cur = intrnd((y_win1-(h_win/2) + frac*h_win))
        pt_i = (compute_x(line1, y_cur), y_cur)
        pt_j = (compute_x(line2, y_cur), y_cur)
        pts.append((pt_i, pt_j))
        
    r1 = solve_for_r1(pts,
                      K,
                      lane_width)
    vanishing_pt = np.cross(line1, line2)
    vanishing_pt = vanishing_pt / vanishing_pt[2]
    vanishing_pt[1] = vanishing_pt[1]
    ## DEBUG Plot points on image, save to file for visual verification
    Irgb = util.to_rgb(I)
    COLOURS = [(255, 0, 0), (0, 255, 0)]
    for i, (pt_i, pt_j) in enumerate(pts):
        clr = COLOURS[i % 2]
        cv2.circle(Irgb, tuple(map(intrnd, pt_i)), 5, clr)
        cv2.circle(Irgb, tuple(map(intrnd, pt_j)), 5, clr)
    cv2.circle(Irgb, (intrnd(vanishing_pt[0]), intrnd(vanishing_pt[1])), 5, (0, 0, 255))
    cv2.imwrite("_Irgb.png", Irgb)

    r3 = solve_for_r3(vanishing_pt, line1, line2, K)
    T = solve_for_t(pts, K, r1, r3, lane_width)
    print "T_pre:", T
    T = T * (2.1798 / T[1])    # Height of camera is 2.1798 meters
    T[2] = 1 # We want the ref. frame to be directly below camera (why 1?!)
    #T = T / np.linalg.norm(T)
    print "T_post:", T
    
    H = np.zeros([3,3])
    H[:, 0] = r1
    H[:, 1] = r3
    H[:, 2] = T
    return np.dot(K, H)

def solve_for_r1(pts, K, lane_width):
    """ Solve for first column of the rotation matrix, utilizing the
    fact that we know the lane width. We require two pairs of points
    (p1,p2), (p3,p4), such that p1 is directly across from p2 (same
    for p3, p4).
    Input:
        tuple pts: ((p1, p2), (p3, p4))
            where each point is a pixel coord: (float x, float y)
        nparray K
            The 3x3 camera intrinsic matrix.
        float lane_width
            The width of the lane (e.g., 3.66 meters).
    Output:
        nparray r1
    A 3x1 column vector consisting of the first column of R.
    """
    (fx, fy, (cx, cy)) = util_camera.get_intrinsics(K)
    # Construct data matrix A
    Araw = np.zeros([len(pts) * 2, 4])
    i = 0 # Actual index into A
    for ii, (pi, pj) in enumerate(pts):
        xi, yi = pi
        xj, yj = pj
        Araw[i, :]   = (0, -fy, (-cy + yj), (yi - yj))
        Araw[i+1, :] = (fx, 0, (cx - xj), (-xj + xi))
        #Araw[i+2, :] = (-yj*fx, xj*fy, -yj*cx + xj*cy, yj*xi - xj*yi)
        i += 2
    rnk = numpy.linalg.matrix_rank(Araw)
    print "    Rank(A):", rnk
    if rnk == 3:
        A = Araw # Perfect! Just the rank we want.
    elif rnk < 3:
        raise Exception("Matrix A needs to have rank either 4 or 3! Rank was: {0}".format(rnk))
    else:
        # A is full rank - perform fixed-rank approx. -> rank 3
        print "(solve_for_r1) A is full rank, performing fixed rank approx..."
        U, S, V = numpy.linalg.svd(Araw)
        if (np.linalg.det(V) < 0):
            # We require U,V to have positive determinant
            print "    U,V had negative determinant, correcting."
            U = -U
            V = -V
        S_part = np.diag([S[0], S[1], S[2], 0]) # Kill last singular value
        S_new = np.zeros([Araw.shape[0], 4])
        S_new[0:4, :] = S_part
        A = np.dot(U, np.dot(S_new, V))
        print "    new rank:", np.linalg.matrix_rank(A)
        if np.linalg.matrix_rank(A) != 3:
            raise Exception("(solve_for_r1) What?! Fixed-rank approx. failed!")
    U, S, V = numpy.linalg.svd(A)
    if (np.linalg.det(V) < 0):
        # We require U,V to have positive determinant
        print "    U,V had negative determinant, correcting."
        U = -U
        V = -V
    v = V[-1, :]
    residual = numpy.linalg.norm(np.dot(A, v.T))
    print "(solve_for_r1) Residual: {0}".format(residual)
    gamma = v[-1]
    v_norm = v / gamma
    r11, r21, r31, _ = v_norm
    return np.array([r11, r21, r31])

def solve_for_r3(vanishing_pt, line1, line2, K):
    """ Solve for the third column r3 of the rotation matrix,
    utilizing the vanishing point of the lanes.
    Input:
        nparray vanishing_pt: [x, y, 1]
            Pixel image location of the vanishing point defined by the
            lanes.
        nparray line1, line2: [a, b, c]
            Lines vectors of the left/right lanes, in the form:
                [a, b, c]
            such that:
                ax + by + c = 0
        nparray K
            Camera intrinsic matrix.
    Output:
        nparray r3
            The third column of the rotation matrix R.
    """
    Kinv = numpy.linalg.inv(K)
    r3 = np.dot(Kinv, vanishing_pt)
    r3_norm = r3 / r3[2]
    return r3_norm

def solve_for_t(pts, K, r1, r3, lane_width):
    """ Recover the translation vector T, using the computed r1, r3.
    The input points pairs must be directly across from the lanes.
    Input:
        tuple pts: ((pt1, pt2), ...)
            Each point pair (pt_i, pt_j) must be directly across the
            lanes i.e. (X_j - X_i) = 3.66 meters, and:
                X_i = -1.83 meters
                X_j = +1.83 meters
        nparray K
            3x3 camera intrinsic matrix.
        nparray r1, r3
            The 3x1 column vectors comprising the first/third columns
            of the rotation matrix R.
        float lane_width
            Width of the lane (in meters).
    Output:
        nparray T
            The translation vector T as a 3x1 column vector.
    """
    (fx, fy, (cx, cy)) = util_camera.get_intrinsics(K)
    Kinv = numpy.linalg.inv(K)
    r11, r21, r31 = r1
    r13, r23, r33 = r3
    ww = lane_width / 2
    # Construct data matrix A
    Araw = np.zeros([len(pts) * 6, 5])
    i = 0 # Actual index into A
    for ii, (pi, pj) in enumerate(pts):
        xi, yi = pi
        xj, yj = pj
        bi = np.array([(xi - cx) / fx,
                       (yi - cy) / fy,
                       1]).T
        bj = np.array([(xj - cx) / fx,
                       (yj - cy) / fy,
                       1]).T
        Araw[i, :]   = [r13, 1, 0, 0, -ww*r11 - bi[0]]
        Araw[i+1, :] = [r23, 0, 1, 0, -ww*r21 - bi[1]]
        Araw[i+2, :] = [r33, 0, 0, 1, -ww*r31 - bi[2]]

        Araw[i+3, :] = [r13, 1, 0, 0, -ww*r11 - bj[0]]
        Araw[i+4, :] = [r23, 0, 1, 0, -ww*r21 - bj[1]]
        Araw[i+5, :] = [r33, 0, 0, 1, -ww*r31 - bj[2]]
        i += 6
    rnk = np.linalg.matrix_rank(Araw)
    if rnk == 4:
        A = Araw # Perfect! Just the rank we want.
    elif rnk < 4:
        raise Exception("(solve_for_r3) Matrix rank needs to be either 5 or 4 (was: {0})".format(rnk))
    else:
        # Perform fixed-rank approx on Araw (want rank 4)
        print "(solve_for_t): Araw has full rank, performing fixed_rank approx..."
        U, S, V = np.linalg.svd(Araw)
        if np.linalg.svd(V) < 0:
            U = -U
            V = -V
        S_new = np.zeros([U.shape[0], 5])
        for i in xrange(4):
            S_new[i,i] = S[i]
        A = np.dot(U, np.dot(S_new, V))
        print np.allclose(Araw, A)
        print Araw[0,:]
        print '=='
        print A[0,:]
        
    print "(solve_for_t): Rank(A):", np.linalg.matrix_rank(A)
    U, S, V = numpy.linalg.svd(A)
    if np.linalg.det(V) < 0:
        U = -U
        V = -V
    v = V[-1, :]
    print "    residual: {0}".format(np.linalg.norm(np.dot(A, v)))
    gamma = v[-1]
    v_norm = v / gamma
    Z, tx, ty, tz, _ = v_norm
    return np.array([tx, ty, tz]).T

def main():
    # K matrix given by the Caltech Lanes dataset (CameraInfo.txt)
    K = np.array([[309.4362,     0,        317.9034],
                  [0,         344.2161,    256.5352],
                  [0,            0,            1   ]])
    line1 = np.array([  1.30459272,     1.,     -589.16024465])
    line2 = np.array([  -1.26464497,    1.,     228.18829664])
    win1 = (0.4, 0.60, 0.2, 0.25)
    win2 = (0.62, 0.60, 0.2, 0.25)
    lane_width = 3.66 # 3.66 meters
    imgpath = 'imgs_sample/f00001.png'
    I = cv2.imread(imgpath, cv2.CV_LOAD_IMAGE_GRAYSCALE)
    #### Draw lines on _Irgbline.png to sanity check lane detection
    Irgb = cv2.imread(imgpath, cv2.CV_LOAD_IMAGE_COLOR)
    Irgb = util_camera.draw_line(Irgb, line1, (0, 255, 0))
    Irgb = util_camera.draw_line(Irgb, line2, (255, 0, 0))
    cv2.imwrite("_Irgbline.png", Irgb)

    H = estimate_planar_homography(I, line1, line2, K, win1, win2, lane_width)
    print H
    print "    rank(H): {0}".format(np.linalg.matrix_rank(H))
    print "    det(H): {0}".format(np.linalg.det(H))
    if np.linalg.matrix_rank(H) == 3:
        print "The following should be identity (inv(H) * H):"
        print np.dot(numpy.linalg.inv(H), H)

    print "(Evaluating a few world points to see where they lie on the image)"
    Irgb = cv2.imread(imgpath, cv2.CV_LOAD_IMAGE_COLOR)
    # world point: (X, Z, 1), i.e. point on world plane (road)
    world_pts = [
        # Points in MIDDLE of lane, going towards horizon
        ((0, 0, 1), (0, 1, 1), (0, 2, 1), (0, 4, 1), (0, 8, 1), (0, 16, 1), (0, 32, 1), (0, 10000, 1)),
        # Points on LEFT lane, going towards horizon
        ((-1.83, 0, 1), (-1.83, 2, 1), (-1.83, 4, 1), (-1.83, 8, 1), (-1.83, 10000, 1)),
        # Points on RIGHT lane, going towards horizon
        ((1.83, 0, 1), (1.83, 2, 1), (1.83, 4, 1), (1.83, 8, 1), (1.83, 10000, 1))
        ]
    colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    for i, sub_pts in enumerate(world_pts):
        clr = colours[i % len(colours)]
        for pt in sub_pts:
            pt_np = np.array(pt)
            pt_img = np.dot(H, pt_np)
            pt_img = pt_img / pt_img[2]
            print "(i={0}) World {1} -> {2}".format(i, pt, pt_img)
            cv2.circle(Irgb, (intrnd(pt_img[0]), intrnd(pt_img[1])), 3, clr)
        print
        
    cv2.imwrite("_Irgb_pts.png", Irgb)

    print "Done."

if __name__ == '__main__':
    main()
