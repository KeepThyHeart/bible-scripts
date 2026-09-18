# Makefile for SWORD Common Library

CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra -O3 -I/usr/include/sword -fPIC
AR = ar
ARFLAGS = rcs

LIBRARY = libswordcommon.a
SOURCE = sword_common.cpp
OBJECT = sword_common.o

.PHONY: all clean

all: $(LIBRARY)

$(OBJECT): $(SOURCE) sword_common.h
	$(CXX) $(CXXFLAGS) -c $(SOURCE) -o $(OBJECT)

$(LIBRARY): $(OBJECT)
	$(AR) $(ARFLAGS) $(LIBRARY) $(OBJECT)

clean:
	rm -f $(OBJECT) $(LIBRARY)
