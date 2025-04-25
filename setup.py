from setuptools import setup, find_packages

setup(
    name="healthagent",
    version="1.0.1",
    packages=find_packages(),
    install_requires=[
        'dbus-next',
    ],
    entry_points={
        'console_scripts': [
            'healthagent = healthagent.main:main',
        ],
    },
)