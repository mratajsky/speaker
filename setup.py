#!/usr/bin/env python3

import os
from setuptools import setup, find_packages

requires_all = [
    'colorlog',
    'grpcio',
    'protobuf',
    'pulsectl',
    'pygobject',
    'redis',
    'zeroconf',
]
requires_cli = [
    'grpcio',
    'protobuf',
    'zeroconf',
]

cli_only = int(os.environ.get('CLI_ONLY', 0))
if cli_only:
    scripts = ['bin/speaker-cli']
    requires = requires_cli
else:
    scripts = ['bin/speaker', 'bin/speaker-cli']
    requires = requires_all

setup(name='speaker',
      author='Michal Ratajsky',
      author_email='michal.ratajsky@gmail.com',
      version='1.0',
      description='Network speaker',
      scripts=scripts,
      packages=find_packages(),
      install_requires=requires,
      classifiers=[
          "Programming Language :: Python :: 3",
          "License :: OSI Approved :: MIT License",
          "Operating System :: OS Independent",
      ],
      python_requires='>=3.7')
