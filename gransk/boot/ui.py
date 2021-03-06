#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Start the gransk web UI."""

from __future__ import absolute_import, unicode_literals

import argparse
import os
import logging
import json
from functools import wraps
import requests

from flask import Flask, Response, request, abort

import gransk.api
import gransk.core.helper as helper
import gransk.core.pipeline as pipeline
import gransk.core.document as document
import gransk.core.injector as injector
import gransk.boot.run

_root = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '..')
app = Flask(
    __name__,
    template_folder=os.path.join(_root, 'gransk', 'web', 'app'),
    static_url_path='/static',
    static_folder=os.path.join(_root, 'gransk', 'web', 'app'))


_globals = {}

def check_auth(username, password):
  if not _globals['config'].get('auth'):
    return True

  return username == _globals['config']['auth']['user'] and password == _globals['config']['auth']['pass']

def authenticate():
    return Response(
    'Could not verify your access level for that URL.\n'
    'You have to login with proper credentials', 401,
    {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
          if _globals.get('test'):
            return f(*args, **kwargs)
          return authenticate()
        return f(*args, **kwargs)
    return decorated

@app.after_request
@requires_auth
def add_header(response):
  """Set HTTP headers on each response."""
  response.headers['Access-Control-Allow-Origin'] = '*'
  response.headers[
      'Access-Control-Allow-Headers'] = 'Accept, Authorization, Content-Type, X-Requested-With, Range'
  response.headers[
      'Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
  response.headers[
      'Access-Control-Allow-Methods'] = 'GET,HEAD,PUT,PATCH,POST,DELETE'
  response.headers['Access-Control-Expose-Headers'] = 'Content-Length'
  response.headers['Pragma'] = 'no-cache'
  response.headers['Expires'] = '-1'
  return response


@app.route('/upload', methods=['POST'])
def upload():
  """Receive and process an uploaded file."""
  _file = request.files.get('file')

  doc = document.get_document(
      _file.filename,
      parent=document.get_document('root'),
      need_secure_path=True)

  doc.tag = 'upload'

  _globals['gransk'].add_file(doc, file_object=_file)

  return Response('ok')


@app.route('/data', methods=['DELETE'])
def delete_data():
  """Clear all processed data."""
  _globals['gransk'].clear_all()

  return Response('ok')


@app.route('/file', methods=['GET'])
def get_file():
  """Get original file."""
  filename = document.secure_path(request.args['filename'])
  ext = document.secure_path(request.args['ext'])
  mediatype = request.args['mediatype']

  root = os.path.join(_globals['gransk'].config[helper.DATA_ROOT], 'files')
  file_path = os.path.join(root, ext, filename)

  if not os.path.exists(file_path):
    abort(404)

  with open(file_path, 'rb') as inp:
    return Response(inp.read(), mimetype=mediatype, status=200)


@app.route('/search')
def search():
  query = json.loads(request.args['q'])
  if 'type' in query:
    url = 'http://%s:9200/gransk/%s/_search?' % (_globals['config']['es_host'][0], query['type'])
  else:
    url = 'http://%s:9200/gransk/_search' % _globals['config']['es_host'][0]

  r = requests.get(url, data=json.dumps(query['body']))

  return Response(r.text, status=200, mimetype='application/json')


@app.route('/picture', methods=['GET'])
def picture():
  """Get document content as picture."""
  name = document.secure_path(request.args['name'])
  mediatype = request.args['mediatype']

  root = os.path.join(_globals['gransk'].config[helper.DATA_ROOT], 'pictures')
  image_path = os.path.join(root, name)

  if not os.path.exists(image_path):
    abort(404)

  with open(image_path, 'rb') as fp:
    return Response(fp.read(), mimetype=mediatype, status=200)


@app.route('/related', methods=['GET'])
def related():
  """Get related documents or entities."""
  if request.args['type'] == 'document':
    service = 'related_documents'
  elif request.args['type'] == 'entity':
    service = 'related_entities'
  else:
    return Response('Invalid type %s' % request.args['type'])

  if not _globals['gransk'].pipeline.get_service(service):
      return Response('{"error": "service not found"}', status=200, mimetype='application/json')

  result = _globals['gransk'].pipeline.get_service(service).get_related_to(
      request.args['id'])

  return Response(json.dumps(result), status=200, mimetype='application/json')


@app.route('/network', methods=['GET'])
def entity_network():
  """Get the generated network around a given entitiy ID."""
  hops = int(request.args.get('hops', 1))

  result = _globals['gransk'].pipeline.get_service('entity_network').get_for(
      request.args['entity_id'], hops=hops)

  return Response(json.dumps(result), status=200, mimetype='application/json')


@app.route('/saveall', methods=['GET'])
def saveall():
  """Save all related data to the disk."""

  for service in ('related_entities', 'related_documents'):
    if _globals['gransk'].pipeline.get_service(service):
      _globals['gransk'].pipeline.get_service(service).save_all()

  return Response('ok')


@app.route('/', methods=['GET'])
def home():
  """Get main page."""
  return app.send_static_file('index.html')


def parse_args():
  """Parse arguments provided from the command line."""
  parser = argparse.ArgumentParser()
  parser.add_argument('--host', default='127.0.0.1')
  parser.add_argument('--port', default=8084, type=int)
  parser.add_argument('--config', '-c', default=None)
  parser.add_argument('--debug', dest='debug', action='store_true')
  parser.set_defaults(debug=False)
  return parser.parse_args()


def setup(args, pipeline, runmod, injector):
  """Load configuration"""
  logging.basicConfig(
      format='[%(asctime)s] [%(levelname)s] %(name)s: %(message)s',
      level=logging.INFO,
      datefmt='%Y-%m-%d %H:%M:%S')

  _globals['gransk'] = gransk.api.API(injector)
  _globals['config'] = _globals['gransk'].config

  if pipeline:
    _globals['gransk'].pipeline = pipeline

  if _globals['gransk'].pipeline.get_service('related_entities'):
    _globals['gransk'].pipeline.get_service('related_entities').load_all(_globals['config'])

  if _globals['gransk'].pipeline.get_service('related_documents'):
    _globals['gransk'].pipeline.get_service('related_documents').load_all(_globals['config'])


if __name__ == '__main__':
  args = parse_args()
  setup(args, None, gransk.boot.run, gransk.core.injector.Injector())

  #context = ('/etc/letsencrypt/live/gransk.com/cert.pem', '/etc/letsencrypt/live/gransk.com/privkey.pem')
  context = None

  app.run(host=args.host, port=args.port, debug=args.debug, threaded=True, ssl_context=context)
  _globals['gransk'].pipeline.stop()
