from setuptools import setup, find_packages

version = '0.1.0'

setup(
    name='ckanext-dcat-patch',
    version=version,
    description='Patch pour ckanext-dcat afin de gérer l\'absence de scheming_datasets',
    long_description='',
    classifiers=[],
    keywords='',
    author='',
    author_email='',
    url='',
    license='AGPL-3.0',
    packages=find_packages(exclude=['ez_setup', 'examples', 'tests']),
    namespace_packages=['ckanext'],
    include_package_data=True,
    zip_safe=False,
    install_requires=[],
    entry_points='''
        [ckan.plugins]
        dcat_patch=ckanext.dcat_patch:DCATPatchPlugin
        dataizen_dcat=ckanext.dcat_patch.dcat_profile:DataizenDCATConfigPlugin
        [ckan.rdf.profiles]
        dataizen_valid=ckanext.dcat_patch.dcat_profile:DataizenValidityProfile
    ''',
)

