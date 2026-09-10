from setuptools import setup, find_packages

setup(
    name='ckanext-ogc',
    version='0.1.0',
    license='AGPL-3.0',
    description='CKAN extension for OGC API integration with pygeoapi',
    packages=find_packages(),
    namespace_packages=['ckanext'],
    entry_points='''
        [ckan.plugins]
        ogc=ckanext.ogc.plugin:OGCPlugin
        
        [ckan.cli]
        pygeoapi-sync=ckanext.ogc.commands:pygeoapi_sync
    ''',
    install_requires=[
        'requests>=2.25.0',
        'psycopg2-binary>=2.8.0',
        'pyyaml>=5.4.0',
    ],
    author='Dataizen',
    author_email='contact@dataizen.eu',
    url='https://dataizen.eu',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
    ],
)



