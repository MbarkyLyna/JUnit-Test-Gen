package com.example.demo;

import org.springframework.stereotype.Service;

@Service
public class UserService {
    private final GreetingService greetingService;

    public UserService(GreetingService greetingService) {
        this.greetingService = greetingService;
    }

    public String welcomeUser(String name) {
        return greetingService.greet(name);
    }

    public boolean isValidName(String name) {
        return name != null && !name.isBlank() && name.length() <= 100;
    }
}
