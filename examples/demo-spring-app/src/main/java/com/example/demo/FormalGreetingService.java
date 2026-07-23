package com.example.demo;

import org.springframework.stereotype.Service;

@Service
public class FormalGreetingService implements GreetingService {
    @Override
    public String greet(String name) {
        if (name == null || name.isBlank()) {
            return "Good day.";
        }
        return "Good day, " + name.trim() + ".";
    }
}
