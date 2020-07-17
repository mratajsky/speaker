#!/usr/bin/env python3

from setuptools import setup, find_packages

setup(name='speaker',
      author='Michal Ratajsky',
      author_email='michal.ratajsky@gmail.com',
      version='1.0',
      description='Network speaker',
      scripts=['bin/speaker', 'bin/speaker-cli'],
      packages=find_packages(),
      install_requires=[
          'colorlog',
          'grpcio',
          'protobuf',
          'pulsectl',
          'pygobject',
          'redis',
          'zeroconf',
      ],
      classifiers=[
          "Programming Language :: Python :: 3",
          "License :: OSI Approved :: MIT License",
          "Operating System :: OS Independent",
      ],
      python_requires='>=3.7')
