from setuptools import setup, find_packages

setup(
    name='balsa',
    version='1.0',
    packages=find_packages(),
    install_requires=[
        'pandas>=0.21, <0.24',
        'numpy>=1.15',
        'numba>=0.35',
        'numexpr>=2.6',
        'matplotlib>=3.0'
    ]
)
