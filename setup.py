from setuptools import setup, find_packages

setup(
    name="async-ai-engineer",
    version="0.1.0",
    description="A Modern Terminal Multiplexer for AI Development Workflows",
    packages=find_packages(where="src"),
    author="mggger",
    author_email="mggis0or1@gmail.com",
    package_dir={"": "src"},
    install_requires=[
        "textual>=0.45.0",
        "click>=8.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.21.0",
            "black>=22.0.0",
            "flake8>=5.0.0",
        ]
    },
    entry_points={
        "console_scripts": [
            # Primary command as documented in README
            "async-ai-engineer=cli.main:main",
            # Short alias
            "aai=cli.main:main",
        ]
    },
    python_requires=">=3.8",
)
