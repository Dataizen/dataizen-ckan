#!/usr/bin/env python3
"""
Script to identify resources with datastore_active=True but no data in datastore

This script checks all CKAN resources and identifies those marked as datastore_active
but that return 404 when accessing the datastore API. It provides links and information
to help locate where the actual data might be.
"""

import json
import logging
import requests
import sys
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DatastoreResourceChecker:
    """Check CKAN resources for datastore inconsistencies"""
    
    # Formats that are typically expected in datastore (tabular data)
    TABULAR_FORMATS = {'CSV', 'TSV', 'TXT', 'XLS', 'XLSX', 'ODS', 'DB', 'SQLITE'}
    
    # Formats that are NOT expected in datastore (binary/structured data)
    NON_TABULAR_FORMATS = {
        'PDF', 'DOC', 'DOCX', 'ODT',  # Documents
        'SHP', 'SHAPEFILE', 'GEOJSON', 'KML', 'KMZ', 'GPX', 'GML',  # Geospatial vector
        'GEOTIFF', 'TIF', 'TIFF', 'PNG', 'JPG', 'JPEG',  # Rasters/images
        'ZIP', 'RAR', '7Z', 'TAR', 'GZ',  # Archives
        'XML', 'JSON', 'YAML',  # Structured data (not tabular)
        'HTML', 'HTM',  # Web content
        'API', 'WMS', 'WFS', 'WMTS',  # Services
    }
    
    def __init__(self, ckan_url: str, ckan_api_key: str = None, filter_tabular_only: bool = True):
        """
        Initialize checker
        
        Args:
            ckan_url: CKAN instance URL
            ckan_api_key: CKAN API key (optional)
            filter_tabular_only: If True, only report issues for tabular formats (CSV, etc.)
        """
        self.ckan_url = ckan_url.rstrip('/')
        self.ckan_api_key = ckan_api_key
        self.headers = {'Authorization': ckan_api_key} if ckan_api_key else {}
        self.filter_tabular_only = filter_tabular_only
        
        logger.info(f"Initialized checker for: {ckan_url}")
        if filter_tabular_only:
            logger.info("Mode: Only checking tabular formats (CSV, XLS, etc.)")
        else:
            logger.info("Mode: Checking all formats")
    
    def get_all_packages(self) -> List[str]:
        """
        Get list of all package names from CKAN
        
        Returns:
            List of package names
        """
        try:
            url = f"{self.ckan_url}/api/action/package_list"
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if result.get('success'):
                packages = result.get('result', [])
                logger.info(f"Found {len(packages)} packages")
                return packages
            else:
                logger.error(f"Failed to get package list: {result.get('error')}")
                return []
        except Exception as e:
            logger.error(f"Error getting package list: {e}")
            return []
    
    def get_package_details(self, package_name: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a package
        
        Args:
            package_name: Name of the package
            
        Returns:
            Package details or None
        """
        try:
            url = f"{self.ckan_url}/api/action/package_show"
            params = {'id': package_name}
            
            response = requests.get(url, params=params, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if result.get('success'):
                return result.get('result')
            else:
                logger.warning(f"Failed to get package {package_name}: {result.get('error')}")
                return None
        except Exception as e:
            logger.warning(f"Error getting package {package_name}: {e}")
            return None
    
    def check_url_accessible(self, url: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a URL is accessible
        
        Args:
            url: URL to check
            
        Returns:
            Tuple of (is_accessible, status_message)
        """
        if not url:
            return False, "No URL provided"
        
        try:
            response = requests.head(url, allow_redirects=True, timeout=10)
            if response.status_code == 200:
                return True, "Accessible"
            elif response.status_code == 404:
                return False, "Not found (404)"
            else:
                return False, f" Status {response.status_code}"
        except requests.exceptions.RequestException as e:
            return False, f" Error: {str(e)[:50]}"
    
    def check_datastore_exists(self, resource_id: str) -> bool:
        """
        Check if a resource actually exists in the datastore
        
        Args:
            resource_id: Resource ID to check
            
        Returns:
            True if exists in datastore, False otherwise
        """
        try:
            url = f"{self.ckan_url}/api/action/datastore_search"
            params = {
                'resource_id': resource_id,
                'limit': 0  # Just check existence, no data needed
            }
            
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            
            # 404 means resource doesn't exist in datastore
            if response.status_code == 404:
                return False
            
            response.raise_for_status()
            
            result = response.json()
            return result.get('success', False)
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return False
            logger.warning(f"HTTP error checking datastore for {resource_id}: {e}")
            return False
        except Exception as e:
            logger.warning(f"Error checking datastore for {resource_id}: {e}")
            return False
    
    def is_tabular_format(self, format_str: str) -> bool:
        """
        Check if a format is typically tabular and should be in datastore
        
        Args:
            format_str: Format string (case-insensitive)
            
        Returns:
            True if format is tabular, False otherwise
        """
        if not format_str:
            return False
        
        format_upper = format_str.upper().strip()
        
        if format_upper in self.TABULAR_FORMATS:
            return True
        if format_upper in self.NON_TABULAR_FORMATS:
            return False
        
        # Unknown format - treat as potentially tabular if it looks like it
        # CSV-like patterns
        if any(pattern in format_upper for pattern in ['CSV', 'TAB', 'DELIMITED']):
            return True
        
        return False
    
    def get_severity(self, resource: Dict[str, Any]) -> str:
        """
        Determine severity level for a missing datastore resource
        
        Args:
            resource: Resource dictionary
            
        Returns:
            'critical', 'warning', or 'info'
        """
        format_str = resource.get('format', '').upper().strip()
        
        if format_str in self.TABULAR_FORMATS:
            return 'critical'  # Tabular format should be in datastore
        elif format_str in self.NON_TABULAR_FORMATS:
            return 'info'  # Non-tabular format - just a config issue
        else:
            return 'warning'  # Unknown format
    
    def check_resources(self, limit: int = None) -> Dict[str, Any]:
        """
        Check all resources for datastore inconsistencies
        
        Args:
            limit: Maximum number of packages to process (for testing)
            
        Returns:
            Dictionary with statistics and problematic resources
        """
        logger.info("Starting resource check...")
        
        packages = self.get_all_packages()
        if limit:
            packages = packages[:limit]
            logger.info(f"Processing first {limit} packages (test mode)")
        
        problematic_resources = []
        stats = {
            'total_packages': len(packages),
            'packages_checked': 0,
            'total_resources': 0,
            'datastore_active_resources': 0,
            'missing_in_datastore': 0,
            'critical_issues': 0,  # Tabular formats missing
            'warnings': 0,  # Unknown formats missing
            'info_issues': 0,  # Non-tabular formats (expected)
            'by_dataset': defaultdict(list),
            'by_severity': defaultdict(list)
        }
        
        for i, package_name in enumerate(packages):
            logger.info(f"Checking package {i+1}/{len(packages)}: {package_name}")
            
            package = self.get_package_details(package_name)
            if not package:
                continue
            
            stats['packages_checked'] += 1
            resources = package.get('resources', [])
            stats['total_resources'] += len(resources)
            
            for resource in resources:
                if resource.get('datastore_active'):
                    stats['datastore_active_resources'] += 1
                    
                    # Check if it actually exists in datastore
                    if not self.check_datastore_exists(resource['id']):
                        format_str = resource.get('format', 'unknown')
                        is_tabular = self.is_tabular_format(format_str)
                        
                        # Filter: if filter_tabular_only and not tabular, skip
                        if self.filter_tabular_only and not is_tabular:
                            continue
                        
                        stats['missing_in_datastore'] += 1
                        
                        # Check if original URL is accessible
                        original_url = resource.get('url', '')
                        url_accessible, url_status = self.check_url_accessible(original_url)
                        
                        # Build download URL
                        download_url = f"{self.ckan_url}/dataset/{package.get('name', '')}/resource/{resource.get('id', '')}/download"
                        
                        # Get resource extras (may contain spatial metadata)
                        extras = resource.get('extras', {})
                        if isinstance(extras, list):
                            # Convert list of dicts to dict
                            extras_dict = {item.get('key', ''): item.get('value', '') for item in extras if isinstance(item, dict)}
                            extras = extras_dict
                        
                        # Check for spatial metadata in extras
                        spatial_extras = {}
                        spatial_keys = ['spatial', 'bbox', 'bbox-east-long', 'bbox-west-long', 'bbox-north-lat', 'bbox-south-lat', 
                                       'crs', 'srid', 'spatial_text', 'spatial_uri', 'geographic_coverage']
                        for key in spatial_keys:
                            if key in extras:
                                spatial_extras[key] = extras[key]
                        
                        # Get file storage path (if available)
                        # CKAN stores uploaded files in ckan_storage/resources/{hash_prefix}/{hash_suffix}/
                        file_path = resource.get('url', '').replace(self.ckan_url, '').replace('http://localhost:8080', '')
                        
                        # Get severity
                        resource_info = {
                            'resource_id': resource.get('id'),
                            'resource_name': resource.get('name', 'Unnamed'),
                            'format': format_str,
                            'url': original_url,
                            'file_path': file_path,
                            'url_accessible': url_accessible,
                            'url_status': url_status,
                            'download_url': download_url,
                            'description': resource.get('description', ''),
                            'created': resource.get('created', ''),
                            'last_modified': resource.get('last_modified', ''),
                            'size': resource.get('size', ''),
                            'package_id': package.get('id', ''),
                            'package_name': package.get('name', ''),
                            'package_title': package.get('title', ''),
                            'package_url': f"{self.ckan_url}/dataset/{package.get('name', '')}",
                            'resource_url': f"{self.ckan_url}/dataset/{package.get('name', '')}/resource/{resource.get('id', '')}",
                            'ckan_api_url': f"{self.ckan_url}/api/action/resource_show?id={resource.get('id', '')}",
                            'is_tabular': is_tabular,
                            'spatial_extras': spatial_extras,
                            'all_extras': extras if extras else {},
                        }
                        
                        # Add severity
                        severity = self.get_severity(resource_info)
                        resource_info['severity'] = severity
                        stats['by_severity'][severity].append(resource_info)
                        
                        if severity == 'critical':
                            stats['critical_issues'] += 1
                        elif severity == 'warning':
                            stats['warnings'] += 1
                        else:
                            stats['info_issues'] += 1
                        
                        problematic_resources.append(resource_info)
                        stats['by_dataset'][package_name].append(resource_info)
                        
                        severity_icon = '' if severity == 'critical' else '' if severity == 'warning' else ''
                        logger.warning(f"  {severity_icon} Resource '{resource_info['resource_name']}' (ID: {resource_info['resource_id']}, "
                                     f"format: {format_str}) has datastore_active=True but no data in datastore")
        
        stats['problematic_resources'] = problematic_resources
        
        logger.info("\n" + "="*80)
        logger.info("CHECK SUMMARY")
        logger.info("="*80)
        logger.info(f"Total packages checked: {stats['packages_checked']}/{stats['total_packages']}")
        logger.info(f"Total resources: {stats['total_resources']}")
        logger.info(f"Resources with datastore_active=True: {stats['datastore_active_resources']}")
        logger.info(f"Resources missing in datastore: {stats['missing_in_datastore']}")
        logger.info("")
        logger.info("By severity:")
        logger.info(f"  Critical (tabular formats): {stats['critical_issues']}")
        logger.info(f"  Warnings (unknown formats): {stats['warnings']}")
        logger.info(f"  Info (non-tabular formats): {stats['info_issues']}")
        
        return stats
    
    def print_report(self, stats: Dict[str, Any], output_format: str = 'text'):
        """
        Print a formatted report
        
        Args:
            stats: Statistics dictionary from check_resources()
            output_format: 'text' or 'json'
        """
        if output_format == 'json':
            print(json.dumps(stats, indent=2, ensure_ascii=False))
            return
        
        # Text format
        print("\n" + "="*80)
        print("DETAILED REPORT: Resources with datastore_active but no datastore data")
        print("="*80)
        
        if not stats['problematic_resources']:
            print("\nNo problematic resources found!")
            return
        
        print(f"\nFound {len(stats['problematic_resources'])} problematic resource(s)\n")
        
        # Group by severity first (critical first)
        severity_order = ['critical', 'warning', 'info']
        severity_labels = {
            'critical': 'CRITICAL',
            'warning': 'WARNING',
            'info': 'INFO'
        }
        
        for severity in severity_order:
            resources_by_severity = stats['by_severity'][severity]
            if not resources_by_severity:
                continue
            
            print(f"\n{'='*80}")
            print(f"{severity_labels[severity]}: {len(resources_by_severity)} resource(s)")
            if severity == 'critical':
                print("These are tabular formats that SHOULD be in datastore")
            elif severity == 'warning':
                print("Unknown formats - review manually")
            else:
                print("Non-tabular formats - consider removing datastore_active flag")
            print("="*80)
            
            # Group by dataset for better readability
            by_dataset = defaultdict(list)
            for res in resources_by_severity:
                by_dataset[res['package_name']].append(res)
            
            for dataset_name, resources in sorted(by_dataset.items()):
                package = resources[0]  # Get package info from first resource
                print(f"\nDATASET: {package['package_title']} ({package['package_name']})")
                print(f"   URL: {package['package_url']}")
                
                for i, resource in enumerate(resources, 1):
                    print(f"\n  Resource #{i}: {resource['resource_name']}")
                    print(f"    ID: {resource['resource_id']}")
                    print(f"    Format: {resource['format']} {'(tabular)' if resource.get('is_tabular') else '(non-tabular)'}")
                    
                    # Data location information
                    print(f"\n    WHERE ARE THE DATA STORED?")
                    if not resource.get('is_tabular'):
                        print(f"       This is a NON-TABULAR format - data is NOT in datastore")
                        print(f"      Data is stored as a FILE on the server")
                        print(f"      Base directory: /var/lib/ckan/resources/ (or ckan_storage/resources)")
                        
                    # Database storage
                    print(f"\n     DATABASE REFERENCES:")
                    print(f"      • Table: 'resource' in database 'ckan'")
                    print(f"      • Resource ID: {resource['resource_id']}")
                    print(f"      • Package ID: {resource['package_id']}")
                    
                    # Spatial metadata in database
                    if resource.get('spatial_extras'):
                        print(f"\n    SPATIAL METADATA (in resource.extras):")
                        for key, value in resource['spatial_extras'].items():
                            print(f"      • {key}: {value}")
                    else:
                        print(f"\n    SPATIAL METADATA: None in resource.extras")
                    
                    # File storage
                    if resource.get('file_path'):
                        print(f"\n    FILE STORAGE PATH:")
                        print(f"      • URL path: {resource['file_path']}")
                        if '/resources/' in resource['file_path']:
                            print(f"      • Likely location: /var/lib/ckan/resources/...")
                            print(f"      • Physical storage: CKAN filesystem (ckan_storage/resources)")
                    else:
                        print(f"\n    FILE STORAGE: External URL or not yet uploaded")
                    
                    # Original URL
                    if resource.get('url'):
                        url_status = resource.get('url_status', 'Unknown')
                        print(f"\n    Original file URL:")
                        print(f"       {resource['url']}")
                        print(f"       Status: {url_status}")
                    
                    # CKAN download URL
                    if resource.get('download_url'):
                        print(f"\n    Download via CKAN:")
                        print(f"       {resource['download_url']}")
                    
                    # CKAN resource page
                    print(f"\n    CKAN Resource page:")
                    print(f"       {resource['resource_url']}")
                    
                    # Additional info
                    if resource.get('description'):
                        desc = resource['description'][:100] + "..." if len(resource['description']) > 100 else resource['description']
                        print(f"\n    Description: {desc}")
                    
                    if resource.get('size'):
                        size_str = f"{resource['size']:,} bytes" if isinstance(resource['size'], int) else str(resource['size'])
                        print(f"    Size: {size_str}")
                    
                    if resource.get('created'):
                        print(f"    Created: {resource['created']}")
                    
                    # Add recommendation
                    print(f"\n    RECOMMENDATION:")
                    if severity == 'critical':
                        print(f"        This {resource['format']} file SHOULD be in datastore.")
                        print(f"       → Consider re-uploading or running datapusher to populate datastore.")
                    elif severity == 'info':
                        print(f"       For {resource['format']} format, data is stored as a file (not in datastore).")
                        print(f"       → Consider removing datastore_active flag to avoid confusion.")
                        print(f"       → Data is accessible at: {resource.get('download_url', resource.get('url', 'N/A'))}")
                    else:
                        print(f"        Review this resource manually.")
        
        print("\n" + "="*80)
        print("END OF REPORT")
        print("="*80)


def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Check CKAN resources for datastore inconsistencies'
    )
    parser.add_argument(
        '--ckan-url',
        required=True,
        help='CKAN instance URL (e.g., http://localhost:5000)'
    )
    parser.add_argument(
        '--ckan-api-key',
        help='CKAN API key (optional, but recommended)'
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Limit number of packages to check (for testing)'
    )
    parser.add_argument(
        '--format',
        choices=['text', 'json'],
        default='text',
        help='Output format (default: text)'
    )
    parser.add_argument(
        '--output',
        help='Output file path (optional, defaults to stdout)'
    )
    parser.add_argument(
        '--all-formats',
        action='store_true',
        help='Check all formats (default: only tabular formats like CSV, XLS)'
    )
    
    args = parser.parse_args()
    
    # Create checker instance
    checker = DatastoreResourceChecker(
        args.ckan_url, 
        args.ckan_api_key,
        filter_tabular_only=not args.all_formats
    )
    
    # Check resources
    stats = checker.check_resources(limit=args.limit)
    
    # Generate report
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            if args.format == 'json':
                json.dump(stats, f, indent=2, ensure_ascii=False)
            else:
                # Redirect stdout to file for text format
                import sys
                original_stdout = sys.stdout
                sys.stdout = f
                checker.print_report(stats, output_format=args.format)
                sys.stdout = original_stdout
        print(f"Report saved to: {args.output}", file=sys.stdout)
    else:
        checker.print_report(stats, output_format=args.format)
    
    # Exit with appropriate code (only critical issues cause failure)
    exit(0 if stats['critical_issues'] == 0 else 1)


if __name__ == '__main__':
    main()

