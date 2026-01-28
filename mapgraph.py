import numpy as np
import heapq
import sys
from scipy.spatial import KDTree
import csv

node_file = 'graph_nodes.csv'
edge_file = 'graph_edges.csv'

nodes = {}
xnodes = {}
r_nodes = {}
edges = {}
r_edges = {}
R = 6731
points = None
tree = None

def haversine_distance(nd1, nd2):
    dlat = np.radians(abs(nodes[nd1][0] - nodes[nd2][0]))
    dlon = np.radians(abs(nodes[nd1][1] - nodes[nd2][1]))
    d = 2 * R * np.arcsin(np.sqrt((np.sin(dlat / 2)**2 + np.cos(np.radians(nodes[nd1][0])) * np.cos(np.radians(nodes[nd2][0])) * np.sin(dlon / 2)**2)))
    return d

def load_nodes(file):
    nf = open(file, 'r')
    nr = csv.reader(nf)
    i = 0
    for row in nr:
        if len(row) != 0:
            if i == 0:
                i = 1
                continue
            row[0] = int(row[0])
            row[1] = float(row[1])
            row[2] = float(row[2])
            nodes[row[0]] = (row[1], row[2])
            r_nodes[row[0]] = (row[1], row[2])
            xnodes[(row[1], row[2])] = row[0]
            edges[row[0]] = []
            r_edges[row[0]] = []
    nf.close()

def load_edges(file):
    ef = open(file, 'r')
    er = csv.reader(ef)
    i = 0
    for row in er:
        if len(row) != 0:
            if i == 0:
                i = 1
                continue
            row[0] = int(row[0])
            row[1] = int(row[1])
            row[2] = float(row[2])
            edges[row[0]].append((row[1], row[2]))
            r_edges[row[1]].append((row[0], row[2]))
    ef.close()


def precompute():
    global points
    global tree
    load_nodes(node_file)
    load_edges(edge_file)

    points = np.array(list(r_nodes.values()))
    tree = KDTree(points)

    print('Pre Computation Complete!')

def nearest_node(loc):
    md, p = tree.query(loc, k=1)
    p = tuple(points[p])
    nrst = xnodes[p]
    #print(f'Node {nrst} found {md * 10**3} m away')
    return nrst

def dijkstras(dest):
    pq = []
    dist = {}
    for n in r_nodes.keys():
        dist[n] = sys.maxsize
    dist[dest] = 0
    heapq.heappush(pq, (0, dest))

    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        
        for v, w in r_edges[u]:
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
                heapq.heappush(pq, (dist[v], v))
    return dist

def reconstruct_path(came_from, cur):
    path = [cur]
    while cur in came_from:
        cur = came_from[cur]
        path.append(cur)
    return path[::-1]

def astar(src, dest):
    open_set = []
    heapq.heappush(open_set, (0, src))

    came_from = {} # path reconstruction
    g_dist = {src: 0}
    f_dist = {src: haversine_distance(src, dest)}

    while open_set:
        _, cur = heapq.heappop(open_set)
        if cur == dest:
            return reconstruct_path(came_from, cur), g_dist[cur]
        for ngh, dist in edges[cur]:
            tg = g_dist[cur] + dist
            if ngh not in g_dist or tg < g_dist[ngh]:
                came_from[ngh] = cur
                g_dist[ngh] = tg
                f_dist[ngh] = tg + haversine_distance(ngh, dest)
                heapq.heappush(open_set, (f_dist[ngh], ngh))
    return None, float('inf')

def optimal_route(src, dest):
    route, length = astar(src, dest)
    #print('heuristic_route:', route)
    #print('Length:', length)
    route_lat = []
    route_lng = []
    if route is not None:
        for n in route:
            route_lat.append(r_nodes[n][0])
            route_lng.append(r_nodes[n][1])
        route_lat.append(None)
        route_lng.append(None)
    return route, length