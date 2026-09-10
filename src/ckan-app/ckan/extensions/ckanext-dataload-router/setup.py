from setuptools import setup, find_packages

setup(
    name='ckanext-dataload-router',
    version='0.1',
    license='AGPL-3.0',
    description='CKAN extension to prepare and route resources to appropriate data loader (xloader or datapusher-plus)',
    packages=find_packages(),
    namespace_packages=['ckanext'],
    entry_points='''
        [ckan.plugins]
        dataload_router=ckanext.dataload_router:DataloadRouter
    ''',
) 