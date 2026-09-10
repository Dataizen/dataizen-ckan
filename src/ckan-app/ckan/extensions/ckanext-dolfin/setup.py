from setuptools import setup, find_packages

setup(
    name='ckanext-dolfin',
    version='0.1',
    license='AGPL-3.0',
    description='Harmonisation semantique DOLFIN pour CKAN : modeles pivot, mapping, generation NGSI-LD / CSV / GeoJSON, suivi de derive',
    packages=find_packages(),
    namespace_packages=['ckanext'],
    include_package_data=True,
    entry_points='''
        [ckan.plugins]
        dolfin=ckanext.dolfin.plugin:DolfinPlugin
    ''',
)
