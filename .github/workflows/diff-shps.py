#!/usr/bin/env python3
import os
import json
import typing
import shutil
import difflib
import tempfile
import logging
import requests
import subprocess
import dotenv
import osgeo.ogr

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
    with open(path) as file:
        return [
            Feature(
                tuple(sorted(feature['properties'].items())),
                Geometry(
                    feature['geometry']['type'],
                    tuple(feature['bbox']),
                    unspooled_coordinates(feature['geometry']),
                ),
            )
            for feature in json.load(file)['features']
        ]

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
        
        base_geojson_path = f'{tempdir}/base.geojson'
        head_geojson_path = f'{tempdir}/head.geojson'

        cmd1 = (
            'ogr2ogr', '-f', 'GeoJSON',
            '-lco', 'WRITE_BBOX=YES',
            '-lco', 'COORDINATE_PRECISION=7',
            base_geojson_path, base_shp_path,
        )
        subprocess.check_call(cmd1)
        
        cmd2 = (
            'ogr2ogr', '-f', 'GeoJSON',
            '-lco', 'WRITE_BBOX=YES',
            '-lco', 'COORDINATE_PRECISION=7',
            head_geojson_path, head_shp_path,
        )
        subprocess.check_call(cmd2)
        
        base_data = load_features(base_geojson_path)
        head_data = load_features(head_geojson_path)
        matcher = difflib.SequenceMatcher(isjunk=None, a=base_data, b=head_data)
        
        diff_lines = []
        
        for (tag, i1, i2, j1, j2) in sorted(matcher.get_opcodes()):
            if tag == 'delete':
                print('Delete', base_data[i1:i2])
                diff_lines.append('- Delete old feature {}'.format(i1+1))
            elif tag == 'insert':
                print('Insert', head_data[j1:j2])
                diff_lines.append('- Ådd new feature {}'.format(j1+1))
            elif tag == 'replace':
                print('Replace', base_data[i1:i2])
                print('   with', head_data[j1:j2])
                diff_lines.append('- Replace old feature {} with new feature {}'.format(i1+1, j1+1))
        
        if diff_lines:
            comment_lines.extend([f'### {stem}:', ''] + diff_lines + [''])

        shutil.rmtree(tempdir)
    
    posted = requests.post(
        comment_url,
        data=json.dumps({'body': '\n'.join(comment_lines)}),
        headers={
            'Authorization': 'token {token}'.format(token=os.environ['API_ACCESS_TOKEN']),
        },
    )
    
    print(posted.json())
