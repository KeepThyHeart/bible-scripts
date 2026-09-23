# Makefile for SWORD Common Library

CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra -O3 -I/usr/include/sword -fPIC
AR = ar
ARFLAGS = rcs

LIBRARY = libswordcommon.a
SOURCES = sword_common.cpp compression.cpp content_digest.cpp schema_bridge.cpp
OBJECTS = $(SOURCES:.cpp=.o)
HEADERS = sword_common.h compression.h content_digest.h schema_bridge.h

# selftest.cpp itself never calls SWORD/libzip functions — it exercises only
# the module-format-v2 additions (compression, content digest, schema bridge)
# without needing a SWORD module or a real Bible repo checkout (see
# selftest.cpp's doc comment) — but it links libswordcommon.a as ONE archive
# member (sword_common.o) that also carries sha256Hex()/deterministicUuid(),
# so -lsword -lzip are still needed to resolve that object file's other
# references.
TEST_TARGET = selftest
TEST_LDFLAGS = -lsword -lsqlite3 -lzip -lz -l:libzstd.so.1

.PHONY: all clean test

all: $(LIBRARY)

%.o: %.cpp $(HEADERS)
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(LIBRARY): $(OBJECTS)
	$(AR) $(ARFLAGS) $(LIBRARY) $(OBJECTS)

$(TEST_TARGET): selftest.cpp $(LIBRARY)
	$(CXX) $(CXXFLAGS) -DSELFTEST_SCHEMA_JS='"$(abspath ../../../../scripts/lib/schema.js)"' \
		-o $(TEST_TARGET) selftest.cpp $(LIBRARY) $(TEST_LDFLAGS)

test: $(TEST_TARGET)
	./$(TEST_TARGET)

clean:
	rm -f $(OBJECTS) $(LIBRARY) $(TEST_TARGET)
