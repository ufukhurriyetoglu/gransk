#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import division, absolute_import, unicode_literals

import os
import logging
import subprocess
from six.moves.urllib.parse import quote as url_quote

import gransk.core.abstract_subscriber as abstract_subscriber
import gransk.core.helper as helper


class Subscriber(abstract_subscriber.Subscriber):
  """
  Class for uploading documents to Apache Tika and reading text response.
  Tika is an open source tool that is capable of parsing a vast number (>200)
  of document formats.
  """
  CONSUMES = [helper.EXTERNAL_EXTRACTOR]

  SERVICES = []

  def _accept(self, doc):
    return doc.meta['size'] < self.max_size

  def setup(self, config):
    """
    Define maximum size of document to upload.

    :param config: Configuration object.
    :type config: ``dict``
    """
    self.config = config

    self.max_size = config.get(helper.TIKA_MAX_SIZE, 1024 * 1024 * 64)
    self.ocr_languages = config.get(helper.OCR_LANGUAGES)
    self.detect_scanned_pdf = config.get(helper.DETECT_SCANNED_PDF, True)

    self.tmp_root = os.path.join(config[helper.DATA_ROOT], 'files', '.tmp')
    self.wid = config[helper.WORKER_ID]

  def consume(self, doc, payload):
    """
    Upload document to Apache Tika and add result to document as text.

    :param doc: Document object.
    :param payload: File pointer beloning to document.
    :type doc: ``gransk.core.document.Document``
    :type payload: ``file``
    """
    if not self._accept(doc):
      return

    filename = os.path.basename(doc.path)
    content_type = doc.meta['Content-Type']

    payload.seek(0)
    data = payload.read()
    doc.set_size(payload.tell())

    if content_type == 'application/pdf' and self.detect_scanned_pdf:
      tmp_path = os.path.join(self.tmp_root,
        '%s-%s.pdf' % (self.wid, doc.docid[0:8]))

      if not os.path.exists(self.tmp_root):
        os.makedirs(self.tmp_root)

      with open(tmp_path, 'wb') as fp:
        fp.write(data)

      if self._is_pdf_scanned(tmp_path):
        data_tiff = self._convert_pdf_to_tiff(tmp_path)

        if data_tiff:
          data = data_tiff
          filename = '%s.tiff' % filename
          content_type = 'image/tiff'

      os.remove(tmp_path)

    headers = {
      'Content-Disposition': 'attachment; filename=%s' % url_quote(filename),
      'Content-type': content_type,
    }

    if self.ocr_languages:
      headers['X-Tika-OCRLanguage'] = self.ocr_languages

    connection = self.config[helper.INJECTOR].get_http_connection()
    connection.request('PUT', '/tika', data, headers)
    response = connection.getresponse()

    try:
      if response.status >= 400:
        logging.error('tika error %d (%s): %s', response.status,
                      response.reason, doc.path)
      else:
        doc.text = response.read().strip().decode('utf-8')
    finally:
      response.close()

  @classmethod
  def _is_pdf_scanned(cls, path):
    pdf_info = cls._get_pdf_info(path)

    if not pdf_info:
      return False

    try:
      page_count = int(pdf_info['pages'])
    except (KeyError, ValueError):
      return False

    images_info = cls._get_pdf_images_info(path)

    if not images_info:
      return False

    if len(set(x['page'] for x in images_info)) != page_count:
      return False

    try:
      page_size_x, _, page_size_y, _ = pdf_info['page size'].split(' ', 3)
      page_size_x = float(page_size_x) / 72
      page_size_y = float(page_size_y) / 72
    except (KeyError, ValueError):
      return False

    if page_size_x > page_size_y:
      page_size_x, page_size_y = page_size_y, page_size_x

    try:
      image_size_x = [int(x['width']) / int(x['x-ppi'])
                      for x in images_info if x['type'] == 'image']
      image_size_y = [int(x['height']) / int(x['y-ppi'])
                      for x in images_info if x['type'] == 'image']
    except KeyError:
      return False

    for i, (x, y) in enumerate(zip(image_size_x, image_size_y)):
      image_size_x[i] = min(x, y)
      image_size_y[i] = max(x, y)

    if max(max([page_size_x - x for x in image_size_x]),
           max([page_size_y - y for y in image_size_y])) > 0.2:
      return False

    return True

  @staticmethod
  def _get_pdf_info(path):
    cmd = ['pdfinfo', path]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate()

    if proc.returncode != 0:
      return None

    if err:
      return None

    info = {}

    for line in out.decode('utf-8').split('\n'):
      line = line.strip()

      if not line:
        continue

      k, v = line.split(':', 1)
      info[k.strip().lower()] = v.strip()

    return info

  @staticmethod
  def _get_pdf_images_info(path):
    cmd = ['pdfimages', '-list', path]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate()

    if proc.returncode != 0:
      return None

    if err:
      return None

    outlines = out.decode('utf-8').strip().split('\n')

    if len(outlines) < 3:
      return None

    header = [x.strip() for x in outlines[0].split()]
    body = [[x.strip() for x in line.split()] for line in outlines[2:]]

    if not header or not body:
      return None

    if len(set(map(len, body))) != 1:
      return None

    if len(header) != len(body[0]):
      return None

    return [dict(zip(header, row)) for row in body]

  @staticmethod
  def _convert_pdf_to_tiff(pdf_file):
    tiff_file = pdf_file + '.tiff'
    cmd = ['gs', '-o', tiff_file, '-sDEVICE=tiffg4', '-r300', pdf_file]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _, _ = proc.communicate()

    if proc.returncode != 0:
      return None

    if not os.path.isfile(tiff_file):
      return None

    with open(tiff_file, 'rb') as fp:
      tiff_content = fp.read()

    os.remove(tiff_file)

    return tiff_content
