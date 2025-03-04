#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from setuptools import setup, find_namespace_packages
from distutils.core import setup

setup(name='ofxstatement-paypal-2',
      version='3.1.0',
      author='Alfystar',
      author_email='alfystar1701@gmail.com',
      url='https://github.com/Alfystar/ofxstatement-paypal.git',
      description=('Plugin for ofxstatement that support conversion from Paypal csv to OFX'),
      long_description=open("README.md").read(),
      long_description_content_type='text/markdown',
      license='Apache License 2.0',
      keywords=['ofx', 'ofxstatement', 'paypal'],
      classifiers=[
          'Development Status :: 5 - Production/Stable',
          'Programming Language :: Python :: 3',
          'Natural Language :: English',
          'Topic :: Office/Business :: Financial :: Accounting',
          'Topic :: Utilities',
          'Environment :: Console',
          'Operating System :: OS Independent'
      ],
      packages=find_namespace_packages(where='src', include=['ofxstatement.plugins']),
      package_dir={'': 'src'},
      entry_points={
          'ofxstatement': ['paypal-convert = ofxstatement.plugins.paypal:PayPalPlugin']
      },
      install_requires=['ofxstatement'],
      include_package_data=True,
      zip_safe=True
      )
