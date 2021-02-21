#!/usr/bin/env python3
import os
import itertools
import tempfile
import logging
import requests
import dotenv
import osgeo.ogr

dotenv.load_dotenv()
logging.basicConfig(level=logging.INFO)

GIS_EXTENSIONS = ('.cpg', '.dbf', '.prj', '.shp', '.shx')

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

if __name__ == '__main__':
    
    for stem in list_compare_stems(os.environ):
        base_files = list_ref_paths(os.environ, stem, os.environ['GITHUB_BASE_REF'])
        head_files = list_ref_paths(os.environ, stem, os.environ['GITHUB_HEAD_REF'])

