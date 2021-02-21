#!/usr/bin/env python3
import os
import pprint
import itertools
import requests
import dotenv

dotenv.load_dotenv()

GIS_EXTENSIONS = ('.cpg', '.dbf', '.prj', '.shp', '.shx')

if __name__ == '__main__':
    compare_url = '{api_url}/repos/{repo}/compare/{base_ref}...{head_ref}'.format(
        api_url=os.environ['GITHUB_API_URL'],
        repo=os.environ['GITHUB_REPOSITORY'],
        base_ref=os.environ['GITHUB_BASE_REF'],
        head_ref=os.environ['GITHUB_HEAD_REF'],
    )
    
    got_compare = requests.get(compare_url, headers={
        'Authorization': 'token {token}'.format(token=os.environ['API_ACCESS_TOKEN']),
    })
    
    paths = [
        f['filename']
        for f in got_compare.json().get('files', [])
        if os.path.splitext(f['filename'])[1].lower() in GIS_EXTENSIONS
    ]
    
    stem_key = lambda path: os.path.splitext(path)[0]
    
    for (path_stem, group) in itertools.groupby(sorted(paths), stem_key):
        dirname = os.path.dirname(path_stem)
        print(path_stem, dirname)
        
        dir_base_url = '{api_url}/repos/{repo}/contents/{dir}?ref={base_ref}'.format(
            api_url=os.environ['GITHUB_API_URL'],
            repo=os.environ['GITHUB_REPOSITORY'],
            base_ref=os.environ['GITHUB_BASE_REF'],
            dir=dirname,
        )
        
        got_dir_base = requests.get(dir_base_url, headers={
            'Authorization': 'token {token}'.format(token=os.environ['API_ACCESS_TOKEN']),
        })
        
        pprint.pprint({
            f['path']: f['download_url']
            for f in got_dir_base.json()
            if os.path.splitext(f['path'])[0] == path_stem
            and os.path.splitext(f['path'])[1].lower() in GIS_EXTENSIONS
        })
        
        dir_head_url = '{api_url}/repos/{repo}/contents/{dir}?ref={head_ref}'.format(
            api_url=os.environ['GITHUB_API_URL'],
            repo=os.environ['GITHUB_REPOSITORY'],
            head_ref=os.environ['GITHUB_HEAD_REF'],
            dir=dirname,
        )
        
        got_dir_head = requests.get(dir_head_url, headers={
            'Authorization': 'token {token}'.format(token=os.environ['API_ACCESS_TOKEN']),
        })
        
        pprint.pprint({
            f['path']: f['download_url']
            for f in got_dir_head.json()
            if os.path.splitext(f['path'])[0] == path_stem
            and os.path.splitext(f['path'])[1].lower() in GIS_EXTENSIONS
        })
