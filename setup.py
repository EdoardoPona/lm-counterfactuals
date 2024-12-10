# make a setup.py file to install the package
from setuptools import setup, find_packages

with open('requirements.txt') as f:
    required = f.read().splitlines()

setup(
    name='ravfogel_lm_counterfacutals',
    version='0.1',
    packages=find_packages(),
    install_requires=required,
    zip_safe=False
)
