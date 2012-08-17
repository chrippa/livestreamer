#!/usr/bin/env python

from setuptools import setup, find_packages
from sys import version_info, platform, prefix

version = "1.1.0"
deps = ["pbs"]

# require argparse on Python <2.7 and <3.2
if (version_info[0] == 2 and version_info[1] < 7) or \
   (version_info[0] == 3 and version_info[1] < 2):
    deps.append("argparse")

setup(name="livestreamer",
      version=version,
      description="CLI program that launches streams from various streaming services in a custom video player",
      url="https://github.com/chrippa/livestreamer",
      author="Christopher Rosell",
      author_email="chrippa@tanuki.se",
      license="BSD",
      packages=["livestreamer", "livestreamer/plugins"],
      package_dir={'': 'src'},
      entry_points={
          "console_scripts": ['livestreamer=livestreamer.cli:main']
      },
      install_requires=deps,
      classifiers=["Operating System :: POSIX",
                   "Operating System :: Microsoft :: Windows",
                   "Environment :: Console",
                   "Development Status :: 5 - Production/Stable",
                   "Topic :: Internet :: WWW/HTTP",
                   "Topic :: Multimedia :: Sound/Audio",
                   "Topic :: Utilities"]
)

# Fix the entry point so that we don't end up in an infinite loop because of multiprocess
if platform == 'win32':
	f = open(prefix + "\\Scripts\\livestreamer-script.py", "r+")
	
	contents = f.readlines()

	push = False
	output = ""
	for index, line in enumerate(contents):
		if push:
			line = " " + line;

		if "sys.exit(" in line:
			line = "if __name__ == '__main__':\n " + line;
			push = True
	
		if line == " )\n":
			push = False

		output = output + line

	f.seek(0)
	f.write(output)
	f.close()

