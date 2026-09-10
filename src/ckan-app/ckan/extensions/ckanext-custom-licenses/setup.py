from setuptools import setup, find_packages

setup(
    name='ckanext-custom-licenses',
    version='0.1.0',
    license='AGPL-3.0',
    description='CKAN extension to load licenses from a custom JSON file',
    packages=find_packages(),
    namespace_packages=['ckanext'],
    entry_points='''
        [ckan.plugins]
        custom_licenses=ckanext.custom_licenses.plugin:CustomLicensesPlugin
    ''',
)

