# Makefile for sword-probe: reads a SWORD module through libsword, independently
# of the converters, for tools/import/sword/verify/swordcheck.py.

CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra -O2 -I/usr/include/sword -I../common
LDFLAGS = -L../common -lswordcommon -lsword -lsqlite3 -lzip

TARGET = sword-probe
SOURCE = sword_probe.cpp
COMMON_LIB = ../common/libswordcommon.a

all: $(TARGET)

$(COMMON_LIB):
	$(MAKE) -C ../common

$(TARGET): $(SOURCE) $(COMMON_LIB)
	$(CXX) $(CXXFLAGS) -o $(TARGET) $(SOURCE) $(LDFLAGS)

clean:
	rm -f $(TARGET)

.PHONY: all clean
