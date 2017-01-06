#!/usr/bin/env python

from setuptools import setup

setup(name="checkflac",
      version="0.0.1",
      description="Ensure your personal CD rips conform to a certain standard",
      url="https://github.com/pR0Ps/check-flac",
      license="MIT",
      classifiers=[
          "Development Status :: 3 - Alpha",
          "Programming Language :: Python :: 3",
      ],
      packages=["checkflac"],
      install_requires=["pytaglib >= 1.4.0"],
      entry_points={'console_scripts': ["check-flac=checkflac.checkflac:main"]}
)
