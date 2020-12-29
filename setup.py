#!/usr/bin/env python

from setuptools import setup

setup(name="checkflac",
      version="0.0.2",
      description="Ensure your personal CD rips conform to a certain standard",
      url="https://github.com/pR0Ps/check-flac",
      license="MIT",
      classifiers=[
          "Development Status :: 3 - Alpha",
          "Programming Language :: Python :: 3",
          "Programming Language :: Python :: 3.3",
          "Programming Language :: Python :: 3.4",
          "Programming Language :: Python :: 3.5",
          "Programming Language :: Python :: 3.6",
          "Programming Language :: Python :: 3.7",
      ],
      py_modules=["checkflac"],
      install_requires=["pytaglib >= 1.4.0"],
      entry_points={'console_scripts': ["check-flac=checkflac:main"]}
)
