CXX ?= g++
AR ?= ar
CXXFLAGS ?= -std=c++17 -Wall -Wextra -O2

.PHONY: all clean

all: libdock_client.a dock_client_example

libdock_client.a: dock_client.o
	$(AR) rcs $@ $^

dock_client_example: example.o libdock_client.a
	$(CXX) $(CXXFLAGS) -o $@ example.o libdock_client.a

dock_client.o: dock_client.cpp dock_client.hpp
	$(CXX) $(CXXFLAGS) -c dock_client.cpp

example.o: example.cpp dock_client.hpp
	$(CXX) $(CXXFLAGS) -c example.cpp

clean:
	rm -f *.o *.a dock_client_example
