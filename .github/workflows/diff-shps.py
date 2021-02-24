#!/usr/bin/env python3
import os
import math
import json
import typing
import shutil
import difflib
import tempfile
import logging
import requests
import dotenv
import osgeo.ogr
import osgeo.osr
import cairo

dotenv.load_dotenv()
logging.basicConfig(level=logging.INFO)

GIS_EXTENSIONS = ('.cpg', '.dbf', '.prj', '.shp', '.shx')

class Point (typing.NamedTuple):
    x: float
    y: float

class Geometry (typing.NamedTuple):
    type: tuple
    bbox: tuple
    coordinates: tuple

class Feature (typing.NamedTuple):
    properties: tuple
    geometry: Geometry

splitext_base = lambda path: os.path.splitext(path)[0]
splitext_ext = lambda path: os.path.splitext(path)[1].lower()

def find_pull_request(env):
    pulls_url = '{api_url}/repos/{repo}/pulls?per_page=1'.format(
        api_url=env['GITHUB_API_URL'],
        repo=env['GITHUB_REPOSITORY'],
    )
    
    while True:
        print(pulls_url)
    
        got_pulls = requests.get(pulls_url, headers={
            'Authorization': 'token {token}'.format(token=env['API_ACCESS_TOKEN']),
        })
        
        for pull in got_pulls.json():
            if not pull['base']['sha'].startswith(env['GITHUB_BASE_REF']):
                continue
            if not pull['head']['sha'].startswith(env['GITHUB_HEAD_REF']):
                continue
            
            return pull['comments_url']
        
        return None

def list_compare_stems(env):
    compare_url = '{api_url}/repos/{repo}/compare/{base_ref}...{head_ref}'.format(
        api_url=env['GITHUB_API_URL'],
        repo=env['GITHUB_REPOSITORY'],
        base_ref=env['GITHUB_BASE_REF'],
        head_ref=env['GITHUB_HEAD_REF'],
    )
    
    got_compare = requests.get(compare_url, headers={
        'Authorization': 'token {token}'.format(token=env['API_ACCESS_TOKEN']),
    })
    
    paths = [
        f['filename']
        for f in got_compare.json().get('files', [])
        if splitext_ext(f['filename']) in GIS_EXTENSIONS
    ]
    
    stems = {splitext_base(path) for path in paths}
    
    return sorted(list(stems))

def list_ref_paths(env, path_stem, ref):
    logging.info(f'{path_stem} @{ref}')

    dir_url = '{api_url}/repos/{repo}/contents/{dir}?ref={ref}'.format(
        api_url=env['GITHUB_API_URL'],
        repo=env['GITHUB_REPOSITORY'],
        dir=os.path.dirname(path_stem),
        ref=ref,
    )
    
    got_dir = requests.get(dir_url, headers={
        'Authorization': 'token {token}'.format(token=env['API_ACCESS_TOKEN']),
    })
    
    files = {
        f['path']: f['download_url']
        for f in got_dir.json()
        if splitext_base(f['path']) == path_stem
        and splitext_ext(f['path']) in GIS_EXTENSIONS
    }
    
    for url in sorted(files.values()):
        logging.info(f'{url}')
    
    return files

def unspooled_coordinates(geometry):
    if geometry['type'] == 'Point':
        # [x, y]
        return Point(*geometry['coordinates'])

    if geometry['type'] in ('MultiPoint', 'LineString'):
        # [[x, y], [...]]
        return tuple([Point(*xy) for xy in geometry['coordinates']])

    if geometry['type'] in ('MultiLineString', 'Polygon'):
        # [[[x, y], [...]], [[...], [...]]]
        return tuple([
            tuple([Point(*xy) for xy in coords])
            for coords in geometry['coordinates']
        ])

    if geometry['type'] == 'MultiPolygon':
        # [[[[x, y], [...]], [[...], [...]]], [[[...], [....]], [[...], [...]]]]
        return tuple([
            tuple([tuple([Point(*xy) for xy in ring]) for ring in geom])
            for geom in geometry['coordinates']
        ])

    raise ValueError(f"Unknown geometry type: {geometry['type']}")

def load_features(path):
    mercator = osgeo.osr.SpatialReference()
    mercator.ImportFromEPSG(3857)

    ds = osgeo.ogr.Open(path)
    layer = ds.GetLayer(0)
    features = list()
    
    for feature in layer:
        geometry = feature.GetGeometryRef()
        geometry.TransformTo(mercator)
        geojson = feature.ExportToJson(True, ['COORDINATE_PRECISION:0'])

        features.append(Feature(
            tuple(sorted(geojson['properties'].items())),
            Geometry(
                geojson['geometry']['type'],
                tuple(map(int, geometry.GetEnvelope())),
                unspooled_coordinates(geojson['geometry']),
            ),
        ))

    return features
    
def combined_bboxes(features):
    lefts, rights, bottoms, tops = zip(*[f.geometry.bbox for f in features])
    return (min(lefts), max(rights), min(bottoms), max(tops))

def line_movements(ctx, coordinates):
    ctx.move_to(*coordinates[0])
    for (x, y) in coordinates[1:]:
        ctx.line_to(x, y)

def point_movements(ctx, coordinates):
    for (x, y) in coordinates:
        radius, _ = ctx.device_to_user_distance(3, 0)
        ctx.move_to(x, y)
        ctx.arc(x, y, radius, 0, math.pi*2)

def draw_geometry(ctx, feature, fill_rgb, stroke_rgb):
    if feature.geometry.type == 'MultiPolygon':
        for geom in feature.geometry.coordinates:
            for coords in geom:
                line_movements(ctx, coords)
                ctx.set_source_rgb(*fill_rgb)
            ctx.fill()
            for coords in geom:
                line_movements(ctx, coords)
                ctx.set_source_rgb(*stroke_rgb)
                ctx.stroke()
    elif feature.geometry.type == 'Polygon':
        for coords in feature.geometry.coordinates:
            line_movements(ctx, coords)
            ctx.set_source_rgb(*fill_rgb)
        ctx.fill()
        for coords in feature.geometry.coordinates:
            line_movements(ctx, coords)
            ctx.set_source_rgb(*stroke_rgb)
            ctx.stroke()
    elif feature.geometry.type == 'MultiLineString':
        for coords in feature.geometry.coordinates:
            line_movements(ctx, coords)
            ctx.set_source_rgb(*stroke_rgb)
            ctx.stroke()
    elif feature.geometry.type == 'LineString':
        line_movements(ctx, feature.geometry.coordinates)
        ctx.set_source_rgb(*stroke_rgb)
        ctx.stroke()
    elif feature.geometry.type == 'MultiPoint':
        point_movements(ctx, feature.geometry.coordinates)
        ctx.set_source_rgb(*fill_rgb)
        ctx.fill()
    elif feature.geometry.type == 'Point':
        point_movements(ctx, [feature.geometry.coordinates])
        ctx.set_source_rgb(*fill_rgb)
        ctx.fill()
    else:
        raise ValueError(feature.geometry.type)

if __name__ == '__main__':

    comment_url = find_pull_request(os.environ)
    comment_lines = []
    
    for stem in list_compare_stems(os.environ):
        base_files = list_ref_paths(os.environ, stem, os.environ['GITHUB_BASE_REF'])
        head_files = list_ref_paths(os.environ, stem, os.environ['GITHUB_HEAD_REF'])
        
        base_list = sorted(list(base_files.keys()))
        head_list = sorted(list(head_files.keys()))
        
        tempdir = tempfile.mkdtemp()
        
        for path in (base_files.keys() & head_files.keys()):
            ext = splitext_ext(path)

            base_url = base_files[path]
            with open(f'{tempdir}/base{ext}', 'wb') as file1:
                logging.info(file1.name)
                file1.write(requests.get(base_url, headers={
                    'Authorization': 'token {token}'.format(token=os.environ['API_ACCESS_TOKEN']),
                }).content)
                if ext == '.shp':
                    base_shp_path = file1.name

            head_url = head_files[path]
            with open(f'{tempdir}/head{ext}', 'wb') as file2:
                logging.info(file2.name)
                file2.write(requests.get(head_url, headers={
                    'Authorization': 'token {token}'.format(token=os.environ['API_ACCESS_TOKEN']),
                }).content)
                if ext == '.shp':
                    head_shp_path = file2.name
        
        base_data = load_features(base_shp_path)
        head_data = load_features(head_shp_path)
        matcher = difflib.SequenceMatcher(isjunk=None, a=base_data, b=head_data)
        
        diff_lines, add_features, rm_features = [], [], []
        
        for (tag, i1, i2, j1, j2) in sorted(matcher.get_opcodes()):
            if tag == 'delete':
                print('Delete', base_data[i1:i2])
                diff_lines.append('- Delete old feature {}'.format(i1+1))
                rm_features.extend(base_data[i1:i2])
            elif tag == 'insert':
                print('Insert', head_data[j1:j2])
                diff_lines.append('- Ådd new feature {}'.format(j1+1))
                add_features.extend(head_data[j1:j2])
            elif tag == 'replace':
                print('Replace', base_data[i1:i2])
                print('   with', head_data[j1:j2])
                diff_lines.append('- Replace old feature {} with new feature {}'.format(i1+1, j1+1))
                rm_features.extend(base_data[i1:i2])
                add_features.extend(head_data[j1:j2])
        
        print('...bbox:', combined_bboxes(add_features + rm_features))
        
        with cairo.SVGSurface(f'{stem}.svg', 400, 400) as surface:
            ctx = cairo.Context(surface)
            ctx.translate(200, 200)
            ctx.scale(400/16000, 400/16000)
            ctx.set_line_width(16000/400)
            ctx.set_operator(cairo.Operator.MULTIPLY)
            ctx.scale(1, -1)

            for feature in add_features:
                draw_geometry(ctx, feature, (.3, 1, 1), (0, .3, 1))

            for feature in rm_features:
                draw_geometry(ctx, feature, (1, .5, .5), (.9, 0, 0))
        
        if diff_lines:
            comment_lines.extend([f'### {stem}:', ''] + diff_lines + [''])

        shutil.rmtree(tempdir)
    
    exit()
    
    posted = requests.post(
        comment_url,
        data=json.dumps({'body': '\n'.join(comment_lines)}),
        headers={
            'Authorization': 'token {token}'.format(token=os.environ['API_ACCESS_TOKEN']),
        },
    )
    
    print(posted.json())
