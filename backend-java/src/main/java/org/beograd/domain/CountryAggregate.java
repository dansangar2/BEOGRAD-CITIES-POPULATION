package org.beograd.domain;

import java.math.BigInteger;

public record CountryAggregate(String code, String name, BigInteger population) {}
