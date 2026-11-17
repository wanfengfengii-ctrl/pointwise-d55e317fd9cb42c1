def cross3(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def triangle_area_vector(a, b, c):
    ab = tuple(b[i] - a[i] for i in range(3))
    ac = tuple(c[i] - a[i] for i in range(3))
    return cross3(ab, ac)


def require_nondegenerate_triangle(a, b, c):
    if all(component == 0 for component in triangle_area_vector(a, b, c)):
        raise ValueError("degenerate three-dimensional triangle")
