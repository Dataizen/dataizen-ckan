from setuptools import setup, find_packages

setup(
    name='ckanext-harvest-xloader',
    version='0.1',
    license='AGPL-3.0',
    description='CKAN extension to trigger Xloader after harvest',
    packages=find_packages(),
    namespace_packages=['ckanext'],
    entry_points='''
        [ckan.plugins]
        harvest_xloader=ckanext.harvest_xloader.plugin:HarvestXloaderPlugin
    ''',
) 