from os import path
from setuptools import setup, find_packages


with open(path.join(path.abspath(path.dirname(__file__)), "README.md")) as f:
    long_description = f.read()


setup(
    name="lnpbp-testkit",
    version="0.1.1",
    url="https://github.com/Kixunil/lnpbp-testkit",
    author="Martin Habovstiak",
    author_email="martin.habovstiak@gmail.com",
    license="MIT",
    description="A framework for writing automated tests of applications using LNP/BP",
    long_description=long_description,
    long_description_content_type="text/markdown",
    keywords="bitcoin lightning-network tests",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Topic :: Software Development :: Testing",
    ],
    packages=find_packages(),
    python_requires=">=3.7.3",
    install_requires=["pyxdg", "python-bitcoinlib", "toml", "requests", "typing-extensions; python_version<'3.8'"],
    extras_require={ },
    entry_points={ },
    zip_safe=False,
)
