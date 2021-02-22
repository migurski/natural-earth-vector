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

class Geometry (typing.NamedTuple):
    type: tuple
    bbox: tuple
    coordinates: tuple

class Feature (typing.NamedTuple):
    properties: tuple
    geometry: Geometry

splitext_base = lambda path: os.path.splitext(path)[0]
splitext_ext = lambda path: os.path.splitext(path)[1].lower()

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
        return tuple(geometry['coordinates'])

    if geometry['type'] in ('MultiPoint', 'LineString'):
        # [[x, y], [...]]
        return tuple(map(tuple, geometry['coordinates']))

    if geometry['type'] in ('MultiLineString', 'Polygon'):
        # [[[x, y], [...]], [[...], [...]]]
        return tuple([
            tuple(map(tuple, coords))
            for coords in geometry['coordinates']
        ])

    if geometry['type'] == 'MultiPolygon':
        # [[[[x, y], [...]], [[...], [...]]], [[[...], [....]], [[...], [...]]]]
        return tuple([
            tuple([tuple(map(tuple, ring)) for ring in geom])
            for geom in geometry['coordinates']
        ])

    raise ValueError(f"Unknown geometry type: {geometry['type']}")

def load_features(path):
    with open(path) as file:
        features = json.load(file)['features']
    
        return [
            tuple(sorted(feature['properties'].items()))
            for feature in features
        ], [
            Geometry(
                feature['geometry']['type'],
                tuple(feature['bbox']),
                unspooled_coordinates(feature['geometry']),
            )
            for feature in features
        ]

if __name__ == '__main__':
    
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
        
        base_props, base_geoms = load_features(base_geojson_path)
        head_props, head_geoms = load_features(head_geojson_path)

        matcher1 = difflib.SequenceMatcher(isjunk=None, a=base_props, b=head_props)
        print(matcher1.get_opcodes())
        
        for (tag, i1, i2, j1, j2) in matcher1.get_opcodes():
            if tag == 'delete':
                print('Delete', base_props[i1:i2])
            elif tag == 'insert':
                print('Insert', head_props[j1:j2])
            elif tag == 'replace':
                print('Replace', base_props[i1:i2])
                print('   with', head_props[j1:j2])

        matcher2 = difflib.SequenceMatcher(isjunk=None, a=base_geoms, b=head_geoms)
        print(matcher2.get_opcodes())
        
        for (tag, i1, i2, j1, j2) in matcher2.get_opcodes():
            if tag == 'delete':
                print('Delete', base_geoms[i1:i2])
            elif tag == 'insert':
                print('Insert', head_geoms[j1:j2])
            elif tag == 'replace':
                print('Replace', base_geoms[i1:i2])
                print('   with', head_geoms[j1:j2])

        shutil.rmtree(tempdir)
