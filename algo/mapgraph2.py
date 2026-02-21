# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#                                                                                                   #
#   Include:                                                                                        #
#   import mapgraph as mp                                                                           #
#   mp.precompute()                                                                                 #
#                                                                                                   #
#   Member Functions:                                                                               #
#   mp.nearest_node(tuple of coordinates (lat, lng)) -> id of nearest node                          #
#   mp.optimal_route(source_node, destination_node) -> route (list of ordered nodes), length        #
#   mp.plot_route(route_lat, route_lng) -> url of plotted graph                                     #
#                                                                                                   #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

from collections import deque
import numpy as np
import heapq
import sys
from scipy.spatial import KDTree
import csv
import io
import os
import base64
import matplotlib
from matplotlib.figure import Figure
import matplotlib.patheffects as pe
matplotlib.use('Agg')
import psutil

script_dir = os.path.dirname(os.path.abspath(__file__))
node_file = 'graph_nodes.csv'
edge_file = 'graph_edges.csv'

nodes = {}
xnodes = {}
r_nodes = {}
c_nodes = {}
edges = {}
r_edges = {}
R = 6731
points = None
tree = None
map_lat = []
map_lng = []

def haversine_distance(nd1, nd2):
    dlat = np.radians(abs(nodes[nd1][0] - nodes[nd2][0]))
    dlon = np.radians(abs(nodes[nd1][1] - nodes[nd2][1]))
    d = 2 * R * np.arcsin(np.sqrt((np.sin(dlat / 2)**2 + np.cos(np.radians(nodes[nd1][0])) * np.cos(np.radians(nodes[nd2][0])) * np.sin(dlon / 2)**2)))
    return d

def load_nodes():
    with open(node_file, 'r') as f:
        nr = csv.DictReader(f)
        for row in nr:
            row['id'] = int(row['id'])
            row['lat'] = float(row['lat'])
            row['lng'] = float(row['lng'])
            row['r'] = int(row['r'])
            nodes[row['id']] = (row['lat'], row['lng'])
            c_nodes[row['id']] = row['r']
            if (row['r'] == 1):
                r_nodes[row['id']] = (row['lat'], row['lng'])
            xnodes[(row['lat'], row['lng'])] = row['id']
            edges[row['id']] = []
            r_edges[row['id']] = []

def load_edges():
    with open(edge_file, 'r') as f:
        er = csv.DictReader(f)
        for row in er:
            row['id1'] = int(row['id1'])
            row['id2'] = int(row['id2'])
            row['length'] = float(row['length'])
            edges[row['id1']].append((row['id2'], row['length']))
            r_edges[row['id2']].append((row['id1'], row['length']))

def generate_map():
    for e1 in edges:
        for e2, d in edges[e1]:
            map_lat.extend([nodes[e1][0], nodes[e2][0], None])
            map_lng.extend([nodes[e1][1], nodes[e2][1], None])

def connected(n):
    queue = deque([n])
    while queue:
        # Dequeue a node from the front of the queue
        node = queue.popleft()
        if c_nodes[node] == 0:
            # Mark node as visited and add to traversal list
            c_nodes[node] = 1
            for c, d in edges[node]:
                if c_nodes[c] == 0:
                    queue.append(c)

def precompute():
    global points
    global tree
    global r_nodes

    load_nodes()
    load_edges()
    generate_map()

    points = np.array(list(r_nodes.values()))
    tree = KDTree(points)

    process = psutil.Process()
    print('Graph size:', len(r_nodes), 'Nodes')
    print('Pre Computation Complete! used memory:', (process.memory_info().rss / 1024**3), 'GB')

def plot_route(route_lat, route_lng):
    fig = Figure()
    ax = fig.subplots()
    ax.plot(map_lat, map_lng, 'b')
    ax.plot(route_lat, route_lng, 'r')
    ax.set_xlim([12.9, 13.0])
    ax.set_ylim([77.55, 77.65])
    ax.set_title('Bengaluru')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)

    plot_url = base64.b64encode(buf.getvalue()).decode('utf8')
    return plot_url

def plot_coords(coords_lat, coords_lng, labels):
    fig = Figure()
    ax = fig.subplots()
    ax.plot(map_lat, map_lng, 'b')
    ax.scatter(coords_lat, coords_lng, c='red', s=10, zorder=2.5)
    for i in range(len(labels)):
        ax.text(coords_lat[i], coords_lng[i], labels[i], fontsize=10, color='red',
            ha='left', va='bottom',
            path_effects=[pe.withStroke(linewidth=3, foreground='white')])
    ax.set_xlim([12.9, 13.0])
    ax.set_ylim([77.55, 77.65])
    ax.set_title('Bengaluru')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)

    plot_url = base64.b64encode(buf.getvalue()).decode('utf8')
    return plot_url

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

def optimal_route_plot(src, dest):
    route, length = astar(src, dest)
    route_lat = []
    route_lng = []
    if route is not None:
        for n in route:
            route_lat.append(r_nodes[n][0])
            route_lng.append(r_nodes[n][1])
        route_lat.append(None)
        route_lng.append(None)
    return route, length, plot_route(route_lat, route_lng)

def optimal_route(src, dest):
    route, length = astar(src, dest)
    #print('heuristic_route:', route)
    #print('Length:', length)
    return route, length