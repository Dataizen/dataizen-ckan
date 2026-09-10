from setuptools import setup, find_packages

version = '0.1.0'

setup(
    name='ckanext-admin-tools',
    version=version,
    description="Outils d'administration pour CKAN (suppression en masse, synchronisations, état des moissonnages)",
    long_description='',
    classifiers=[],
    keywords='',
    author='Dataizen',
    author_email='contact@dataizen.eu',
    url='https://dataizen.eu',
    license='AGPL-3.0',
    packages=find_packages(exclude=['ez_setup', 'examples', 'tests']),
    namespace_packages=['ckanext'],
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        # -*- Extra requirements: -*-
    ],
    entry_points='''
        [ckan.plugins]
        admin_tools=ckanext.admin_tools.plugin:AdminToolsPlugin
    ''',
)

