"""Routes bundles of ports (river routing).

get bundle is the generic river routing function
get_bundle calls different function depending on the port orientation.

 - get_bundle_same_axis: ports facing each other with arbitrary pitch on each side
 - get_bundle_corner: 90Deg / 270Deg between ports with arbitrary pitch
 - get_bundle_udirect: ports with direct U-turns
 - get_bundle_uindirect: ports with indirect U-turns

"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

import numpy as np
from numpy import ndarray

import gdsfactory as gf
from gdsfactory.components.bend_euler import bend_euler
from gdsfactory.components.straight import straight as straight_function
from gdsfactory.components.via_corner import via_corner
from gdsfactory.components.wire import wire_corner
from gdsfactory.port import Port
from gdsfactory.routing.get_bundle_corner import get_bundle_corner
from gdsfactory.routing.get_bundle_from_steps import get_bundle_from_steps
from gdsfactory.routing.get_bundle_from_waypoints import get_bundle_from_waypoints
from gdsfactory.routing.get_bundle_sbend import get_bundle_sbend
from gdsfactory.routing.get_bundle_u import get_bundle_udirect, get_bundle_uindirect
from gdsfactory.routing.get_route import get_route, get_route_from_waypoints
from gdsfactory.routing.manhattan import generate_manhattan_waypoints
from gdsfactory.routing.path_length_matching import path_length_matched_points
from gdsfactory.routing.sort_ports import get_port_x, get_port_y
from gdsfactory.routing.sort_ports import sort_ports as sort_ports_function
from gdsfactory.routing.validation import (
    is_invalid_bundle_topology,
    make_error_traces,
    validate_connections,
)
from gdsfactory.typings import (
    ComponentSpec,
    Coordinates,
    CrossSectionSpec,
    MultiCrossSectionAngleSpec,
    Route,
    Step,
)


def get_bundle(
    ports1: list[Port],
    ports2: list[Port],
    separation: float = 3.0,
    extension_length: float = 0.0,
    straight: ComponentSpec = straight_function,
    bend: ComponentSpec = bend_euler,
    with_sbend: bool = False,
    sort_ports: bool = True,
    cross_section: None | CrossSectionSpec | MultiCrossSectionAngleSpec = "xs_sc",
    start_straight_length: float | None = None,
    end_straight_length: float | None = None,
    path_length_match_loops: int | None = None,
    path_length_match_extra_length: float = 0.0,
    path_length_match_modify_segment_i: int = -2,
    enforce_port_ordering: bool = True,
    steps: list[Step] | None = None,
    waypoints: Coordinates | None = None,
    auto_widen: bool = False,
    auto_widen_minimum_length: float = 100,
    taper_length: float = 10,
    width_wide: float = 2,
    **kwargs,
) -> list[Route]:
    """Returns list of routes to connect two groups of ports.

    Routes connect a bundle of ports with a river router.
    Chooses the correct routing function depending on port angles.

    Args:
        ports1: list of starting ports.
        ports2: list of end ports.
        separation: bundle separation (center to center). Defaults to cross_section.width + cross_section.gap
        extension_length: adds straight extension.
        bend: function for the bend. Defaults to euler.
        with_sbend: use s_bend routing when there is no space for manhattan routing.
        sort_ports: sort port coordinates.
        cross_section: CrossSection or function that returns a cross_section.
        start_straight_length: straight length at the beginning of the route. If None, uses default value for the routing CrossSection.
        end_straight_length: end length at the beginning of the route. If None, uses default value for the routing CrossSection.
        path_length_match_loops: Integer number of loops to add to bundle \
                for path length matching. Path-length matching won't be attempted if this is set to None.
        path_length_match_extra_length: Extra length to add to path length matching loops \
                (requires path_length_match_loops != None).
        path_length_match_modify_segment_i: Index of straight segment to add path length matching loops to \
                (requires path_length_match_loops != None).
        enforce_port_ordering: If True, enforce that the ports are connected in the specific order.
        steps: specify waypoint steps to route using get_bundle_from_steps.
        waypoints: specify waypoints to route using get_bundle_from_steps.

    Keyword Args:
        width: main layer waveguide width (um).
        layer: main layer for waveguide.
        width_wide: wide waveguides width (um) for low loss routing.
        auto_widen: taper to wide waveguides for low loss routing.
        auto_widen_minimum_length: minimum straight length for auto_widen.
        taper_length: taper_length for auto_widen.
        bbox_layers: list of layers for rectangular bounding box.
        bbox_offsets: list of bounding box offsets.
        cladding_layers: list of layers to extrude.
        cladding_offsets: list of offset from main Section edge.
        radius: bend radius (um).
        sections: list of Sections(width, offset, layer, ports).
        port_names: for input and output ('o1', 'o2').
        port_types: for input and output: electrical, optical, vertical_te ...
        min_length: defaults to 1nm = 10e-3um for routing.
        snap_to_grid: can snap points to grid when extruding the path.

    .. plot::
        :include-source:

        import gdsfactory as gf

        @gf.cell
        def test_north_to_south():
            dy = 200.0
            xs1 = [-500, -300, -100, -90, -80, -55, -35, 200, 210, 240, 500, 650]

            pitch = 10.0
            N = len(xs1)
            xs2 = [-20 + i * pitch for i in range(N // 2)]
            xs2 += [400 + i * pitch for i in range(N // 2)]

            a1 = 90
            a2 = a1 + 180

            ports1 = [gf.Port(f"top_{i}", center=(xs1[i], +0), width=0.5, orientation=a1, layer=(1,0)) for i in range(N)]
            ports2 = [gf.Port(f"bot_{i}", center=(xs2[i], dy), width=0.5, orientation=a2, layer=(1,0)) for i in range(N)]

            c = gf.Component()
            routes = gf.routing.get_bundle(ports1, ports2)
            for route in routes:
                c.add(route.references)

            return c


        gf.config.set_plot_options(show_subports=False)
        c = test_north_to_south()
        c.plot()

    """

    if isinstance(cross_section, list | tuple):
        xs_list = []
        for element in cross_section:
            xs, angles = element
            xs = gf.get_cross_section(xs, **kwargs)
            xs_list.append((xs, angles))
        cross_section = xs_list

    elif cross_section:
        cross_section = gf.get_cross_section(cross_section)
        cross_section = cross_section.copy(**kwargs)

    # convert single port to list
    if isinstance(ports1, Port):
        ports1 = [ports1]

    if isinstance(ports2, Port):
        ports2 = [ports2]

    # convert ports dict to list
    if isinstance(ports1, dict):
        ports1 = list(ports1.values())

    if isinstance(ports2, dict):
        ports2 = list(ports2.values())

    for p in ports1:
        p.orientation = (
            int(p.orientation) % 360 if p.orientation is not None else p.orientation
        )

    for p in ports2:
        p.orientation = (
            int(p.orientation) % 360 if p.orientation is not None else p.orientation
        )

    if len(ports1) != len(ports2):
        raise ValueError(f"ports1={len(ports1)} and ports2={len(ports2)} must be equal")

    start_port_angles = {p.orientation for p in ports1}
    if len(start_port_angles) > 1:
        raise ValueError(f"All start port angles {start_port_angles} must be equal")

    if sort_ports:
        ports1, ports2 = sort_ports_function(
            ports1, ports2, enforce_port_ordering=enforce_port_ordering
        )
    if enforce_port_ordering and is_invalid_bundle_topology(ports1, ports2):
        return make_error_traces(
            ports1,
            ports2,
            "Not a routable bundle topology! Try flipping the port order of either ports1 or ports2",
        )

    path_length_match_params = {
        "path_length_match_loops": path_length_match_loops,
        "path_length_match_extra_length": path_length_match_extra_length,
        "path_length_match_modify_segment_i": path_length_match_modify_segment_i,
    }
    params = {
        "ports1": ports1,
        "ports2": ports2,
        "separation": separation,
        "bend": bend,
        "straight": straight,
        "cross_section": cross_section,
        "enforce_port_ordering": enforce_port_ordering,
        "auto_widen": auto_widen,
        "auto_widen_minimum_length": auto_widen_minimum_length,
        "taper_length": taper_length,
        "width_wide": width_wide,
    }
    if path_length_match_loops is not None:
        params |= path_length_match_params
    if end_straight_length is not None:
        params["end_straight_length"] = end_straight_length
    if start_straight_length is not None:
        params["start_straight_length"] = start_straight_length

    start_angle = ports1[0].orientation
    end_angle = ports2[0].orientation

    start_axis = "X" if start_angle in [0, 180] else "Y"
    end_axis = "X" if end_angle in [0, 180] else "Y"

    x_start = np.mean([p.x for p in ports1])
    x_end = np.mean([p.x for p in ports2])

    y_start = np.mean([p.y for p in ports1])
    y_end = np.mean([p.y for p in ports2])

    if steps:
        params["steps"] = steps
        return get_bundle_from_steps(**params)

    elif waypoints:
        params["waypoints"] = waypoints
        params.pop("enforce_port_ordering")
        return get_bundle_from_waypoints(**params)

    if start_axis != end_axis:
        return get_bundle_corner(**params)
    if (
        start_angle == 0
        and end_angle == 180
        and x_start < x_end
        or start_angle == 180
        and end_angle == 0
        and x_start > x_end
        or start_angle == 90
        and end_angle == 270
        and y_start < y_end
        or start_angle == 270
        and end_angle == 90
        and y_start > y_end
    ):
        # print("get_bundle_same_axis")
        if with_sbend:
            return get_bundle_sbend(
                ports1,
                ports2,
                sort_ports=sort_ports,
                cross_section=cross_section,
                axis=start_axis
            )
        return get_bundle_same_axis(**params)

    elif start_angle == end_angle:
        # print('get_bundle_udirect')
        return get_bundle_udirect(**params)

    elif end_angle == (start_angle + 180) % 360:
        # print("get_bundle_uindirect")
        params_without_pathlength = {
            k: v for k, v in params.items() if k not in path_length_match_params
        }
        return get_bundle_uindirect(
            extension_length=extension_length, **params_without_pathlength
        )
    else:
        raise NotImplementedError("This should never happen")


def get_port_width(port: Port) -> float | int:
    return port.width


def are_decoupled(
    x1: float,
    x1p: float,
    x2: float,
    x2p: float,
    sep: str | float = "metal_spacing",
) -> bool:
    sep = gf.get_constant(sep)
    if x2p + sep > x1:
        return False
    return False if x2 < x1p + sep else x2 >= x1p - sep


def get_bundle_same_axis(
    ports1: list[Port],
    ports2: list[Port],
    separation: float = 5.0,
    end_straight_length: float = 0.0,
    start_straight_length: float = 0.0,
    bend: ComponentSpec = bend_euler,
    straight: ComponentSpec = straight_function,
    sort_ports: bool = True,
    path_length_match_loops: int | None = None,
    path_length_match_extra_length: float = 0.0,
    path_length_match_modify_segment_i: int = -2,
    cross_section: CrossSectionSpec | MultiCrossSectionAngleSpec = "xs_sc",
    enforce_port_ordering: bool = True,
    auto_widen: bool = False,
    auto_widen_minimum_length: float = 100,
    taper_length: float = 10,
    width_wide: float = 2,
    **kwargs,
) -> list[Route]:
    r"""Semi auto-routing for two lists of ports.

    Args:
        ports1: first list of ports.
        ports2: second list of ports.
        separation: minimum separation between two straights.
        end_straight_length: offset to add at the end of each straight.
        start_straight_length: in um.
        bend: spec.
        sort_ports: sort the ports according to the axis.
        path_length_match_loops: Integer number of loops to add to bundle \
                for path length matching (won't try to match if None).
        path_length_match_extra_length: Extra length to add to path length matching loops \
                (requires path_length_match_loops != None).
        path_length_match_modify_segment_i: Index of straight segment to add path
            length matching loops to (requires path_length_match_loops != None).
        cross_section: CrossSection or function that returns a cross_section.
        enforce_port_ordering: If True, will enforce that the ports are conneceted as ordered.
        kwargs: cross_section settings.


    Returns:
        `[route_filter(r) for r in routes]` list of lists of coordinates
        e.g with default `get_route_from_waypoints`,
        returns a list of elements which can be added to a component


    The routing assumes manhattan routing between the different ports.
    The strategy is to modify `start_straight` and `end_straight` for each
    straight such that straights do not collide.

    .. code::

        1             X    X     X  X X  X
        |-----------|    |     |  | |  |-----------------------|
        |          |-----|     |  | |---------------|          |
        |          |          ||  |------|          |          |
        2 X          X          X          X          X          X


    ports1: at the top
    ports2: at the bottom

    The general strategy is:
    Group tracks which would collide together and apply the following method
    on each group:

        if x2 >= x1, increase ``end_straight``
            (as seen on the right 3 ports)
        otherwise, decrease ``end_straight``
            (as seen on the first 2 ports)

    We deal with negative end_straight by doing at the end
        end_straights = end_straights - min(end_straights)

    This method deals with different metal track/wg/wire widths too.

    """
    _p1 = ports1.copy()
    _p2 = ports2.copy()
    kwargs.pop("straight", None)
    if len(ports1) != len(ports2):
        raise ValueError(f"ports1={len(ports1)} and ports2={len(ports2)} must be equal")
    if sort_ports:
        ports1, ports2 = sort_ports_function(
            ports1, ports2, enforce_port_ordering=enforce_port_ordering
        )

    routes = _get_bundle_waypoints(
        ports1,
        ports2,
        separation=separation,
        bend=bend,
        cross_section=cross_section,
        end_straight_length=end_straight_length,
        start_straight_length=start_straight_length,
        **kwargs,
    )
    if path_length_match_loops:
        routes = [np.array(route) for route in routes]
        routes = path_length_matched_points(
            routes,
            extra_length=path_length_match_extra_length,
            bend=bend,
            nb_loops=path_length_match_loops,
            modify_segment_i=path_length_match_modify_segment_i,
            cross_section=cross_section,
            **kwargs,
        )
    routes = [
        get_route_from_waypoints(
            route,
            bend=bend,
            cross_section=cross_section,
            straight=straight,
            auto_widen=auto_widen,
            auto_widen_minimum_length=auto_widen_minimum_length,
            taper_length=taper_length,
            width_wide=width_wide,
            **kwargs,
        )
        for route in routes
    ]
    if enforce_port_ordering:
        return validate_connections(_p1, _p2, routes)
    return routes


def _get_bundle_waypoints(
    ports1: list[Port],
    ports2: list[Port],
    separation: float = 30,
    end_straight_length: float = 0.0,
    start_straight_length: float = 0.0,
    cross_section: CrossSectionSpec = "xs_sc",
    **kwargs,
) -> list[ndarray]:
    """Returns route coordinates List.

    Args:
        ports1: list of starting ports.
        ports2: list of end ports.
        separation: route spacing.
        end_straight_length: adds a straight.
        start_straight_length: length of straight.
        cross_section: CrossSection or function that returns a cross_section.
        kwargs: cross_section settings.

    """
    if not ports1 and not ports2:
        return []

    assert len(ports1) == len(
        ports2
    ), f"ports1={len(ports1)} and ports2={len(ports2)} must be equal"

    if not ports1 or not ports2:
        print(f"WARNING! ports1={ports1} or ports2={ports2} are empty")
        return []

    axis = "X" if ports1[0].orientation in [0, 180] else "Y"
    if len(ports1) == 1 and len(ports2) == 1:
        return [
            generate_manhattan_waypoints(
                ports1[0],
                ports2[0],
                start_straight_length=start_straight_length,
                end_straight_length=end_straight_length,
                cross_section=cross_section,
                **kwargs,
            )
        ]

    # Contains end_straight of tracks which need to be adjusted together
    end_straights_in_group = []

    # Once a group is finished, all the lengths are appended to end_straights
    end_straights = []

    # Keep track of how many ports should be routed together

    if axis in {"X", "x"}:
        x1_prev = get_port_y(ports1[0])
        x2_prev = get_port_y(ports2[0])
        y0 = get_port_x(ports2[0])
        y1 = get_port_x(ports1[0])
    else:  # X axis
        x1_prev = get_port_x(ports1[0])
        x2_prev = get_port_x(ports2[0])
        y0 = get_port_y(ports2[0])
        y1 = get_port_y(ports1[0])

    s = sign(y0 - y1)
    curr_end_straight = 0

    end_straight_length = end_straight_length or 15.0

    Le = end_straight_length

    # First pass - loop on all the ports to find the tentative end_straights
    for i in range(len(ports1)):
        if axis in {"X", "x"}:
            x1 = get_port_y(ports1[i])
            x2 = get_port_y(ports2[i])
            y = get_port_x(ports2[i])
        else:
            x1 = get_port_x(ports1[i])
            x2 = get_port_x(ports2[i])
            y = get_port_y(ports2[i])

        if are_decoupled(x2, x2_prev, x1, x1_prev, sep=separation):
            # If this metal track does not impact the previous one, then start a new
            # group.
            L = min(end_straights_in_group)
            end_straights += [max(x - L, 0) + Le for x in end_straights_in_group]

            # Start new group
            end_straights_in_group = []
            curr_end_straight = 0

        elif x2 >= x1:
            curr_end_straight += separation
        else:
            curr_end_straight -= separation

        end_straights_in_group.append(curr_end_straight + (y - y0) * s)

        x1_prev = x1
        x2_prev = x2

    # Append the last group
    L = min(end_straights_in_group)

    end_straights += [max(x - L, 0) + Le for x in end_straights_in_group]

    # Second pass - route the ports pairwise
    N = len(ports1)
    return [
        generate_manhattan_waypoints(
            ports1[i],
            ports2[i],
            start_straight_length=start_straight_length,
            end_straight_length=end_straights[i],
            cross_section=cross_section,
            **kwargs,
        )
        for i in range(N)
    ]


def compute_ports_max_displacement(ports1: list[Port], ports2: list[Port]) -> float:
    if ports1[0].orientation in [0, 180]:
        a1 = [p.y for p in ports1]
        a2 = [p.y for p in ports2]
    else:
        a1 = [p.x for p in ports1]
        a2 = [p.x for p in ports2]

    return max(abs(max(a1) - min(a2)), abs(min(a1) - max(a2)))


def sign(x: float) -> int:
    return 1 if x > 0 else -1


def get_min_spacing(
    ports1: list[Port],
    ports2: list[Port],
    sep: float = 5.0,
    radius: float = 5.0,
    sort_ports: bool = True,
) -> float:
    """Returns the minimum amount of spacing in um required to create a \
    fanout.

    Args:
        ports1: list of ports.
        ports2: list of ports.
        sep: separation between the ports.
        radius: bend radius.
        sort_ports: sort the ports according to the axis.

    """
    axis = "X" if ports1[0].orientation in [0, 180] else "Y"
    j = 0
    min_j = 0
    max_j = 0
    if sort_ports:
        if axis in {"X", "x"}:
            ports1.sort(key=get_port_y)
            ports2.sort(key=get_port_y)
        else:
            ports1.sort(key=get_port_x)
            ports2.sort(key=get_port_x)

    for port1, port2 in zip(ports1, ports2):
        if axis in {"X", "x"}:
            x1 = get_port_y(ports1)
            x2 = get_port_y(port2)
        else:
            x1 = get_port_x(port1)
            x2 = get_port_x(port2)
        if x2 >= x1:
            j += 1
        else:
            j -= 1
        if j < min_j:
            min_j = j
        if j > max_j:
            max_j = j
    j = 0

    return (max_j - min_j) * sep + 2 * radius + 1.0


def get_bundle_same_axis_no_grouping(
    ports1: list[Port],
    ports2: list[Port],
    sep: float = 5.0,
    route_filter: Callable = get_route,
    start_straight_length: float | None = None,
    end_straight_length: float | None = None,
    sort_ports: bool = True,
    cross_section: CrossSectionSpec = "xs_sc",
    **kwargs,
) -> list[Route]:
    r"""Returns a list of route elements.

    Compared to get_bundle_same_axis, this function does not do any grouping.
    It is not as smart for the routing, but it can fall back on arclinarc
    connection if needed. We can also specify longer start_straight and end_straight

    Semi auto routing for optical ports
    The routing assumes manhattan routing between the different ports.
    The strategy is to modify ``start_straight`` and ``end_straight`` for each
    straight such that straights do not collide.


    We want to connect something like this:


    .. code::

         2             X    X     X  X X  X
           |-----------|    |     |  | |  |-----------------------|
           |          |-----|     |  | |---------------|          |
           |          |          ||  |------|          |          |
         1 X          X          X          X          X          X

    ``start`` is at the bottom
    ``end`` is at the top

    The general strategy is:

    if x2 < x1, decrease ``start straight``, and increase ``end_straight`` (as seen on left two ports)
    otherwise, decrease ``start_straight``, and increase ``end_straight`` (as seen on the last 3 right ports)

    Args:
        ports1: first list of optical ports.
        ports2: second list of optical ports.
        axis: specifies "X" or "Y" direction along which the port is going.
        route_filter: ManhattanExpandedWgConnector or ManhattanWgConnector \
                or any other connector function with the same input.
        radius: bend radius. If unspecified, uses the default radius.
        start_straight_length: offset on the starting length before the first bend.
        end_straight_length: offset on the ending length after the last bend.
        sort_ports: True -> sort the ports according to the axis. False -> no sort applied.
        cross_section: CrossSection or function that returns a cross_section.
        kwargs: cross_section settings.

    Returns:
        a list of routes the connecting straights.

    """
    axis = "X" if ports1[0].orientation in [0, 180] else "Y"
    elems = []
    j = 0

    # min and max offsets needed for avoiding collisions between straights
    min_j = 0
    max_j = 0

    if sort_ports:
        if axis in {"X", "x"}:
            ports1.sort(key=get_port_y)
            ports2.sort(key=get_port_y)
        else:
            ports1.sort(key=get_port_x)
            ports2.sort(key=get_port_x)

    # Compute max_j and min_j
    for i in range(len(ports1)):
        if axis in {"X", "x"}:
            x1 = ports1[i].center[1]
            x2 = ports2[i].center[1]
        else:
            x1 = ports1[i].center[0]
            x2 = ports2[i].center[0]
        if x2 >= x1:
            j += 1
        else:
            j -= 1
        if j < min_j:
            min_j = j
        if j > max_j:
            max_j = j
    j = 0

    if start_straight_length is None:
        start_straight_length = 0.2

    if end_straight_length is None:
        end_straight_length = 0.2

    start_straight_length += max_j * sep
    end_straight_length += -min_j * sep

    # Do case with wire direct if the ys are close to each other
    for i, _ in enumerate(ports1):
        if axis in {"X", "x"}:
            x1 = ports1[i].center[1]
            x2 = ports2[i].center[1]
        else:
            x1 = ports1[i].center[0]
            x2 = ports2[i].center[0]

        s_straight = start_straight_length - j * sep
        e_straight = j * sep + end_straight_length

        elems += [
            route_filter(
                ports1[i],
                ports2[i],
                start_straight_length=s_straight,
                end_straight_length=e_straight,
                cross_section=cross_section,
                **kwargs,
            )
        ]

        if x2 >= x1:
            j += 1
        else:
            j -= 1
    return elems


get_bundle_electrical = partial(
    get_bundle, bend=wire_corner, cross_section="xs_metal_routing"
)

get_bundle_electrical_multilayer = partial(
    get_bundle,
    bend=via_corner,
    cross_section=[
        (gf.cross_section.metal2, (90, 270)),
        ("xs_metal_routing", (0, 180)),
    ],
)


if __name__ == "__main__":
    # import gdsfactory as gf

    # c = gf.Component("get_bundle_multi_layer")
    # columns = 2
    # ptop = c << gf.components.pad_array(columns=columns)
    # pbot = c << gf.components.pad_array(orientation=90, columns=columns)

    # ptop.movex(300)
    # ptop.movey(300)
    # routes = gf.routing.get_bundle_electrical_multilayer(
    #     ptop.ports, pbot.ports, end_straight_length=100, separation=20
    # )
    # for route in routes:
    #     c.add(route.references)

    # c.show()
    c = gf.Component("demo")
    c1 = c << gf.components.mmi2x2()
    c2 = c << gf.components.mmi2x2()
    c2.move((100, 80))
    bend = partial(gf.components.bend_euler, cross_section="xs_rc")
    straight = partial(gf.components.straight, cross_section="xs_rc")
    routes = get_bundle(
        [c1.ports["o2"], c1.ports["o1"]],
        [c2.ports["o1"], c2.ports["o2"]],
        straight=straight,
        bend=bend,
        cross_section=None,
        # separation=20,
        # separation=10
        # layer=(2, 0),
        # straight=partial(gf.components.straight, layer=(2, 0), width=1),
    )
    for route in routes:
        c.add(route.references)
    c.show(show_ports=True)
