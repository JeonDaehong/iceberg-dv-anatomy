JAR=/root/opt/jars-baseline/iceberg-spark-runtime-4.0_2.13-1.11.0.jar
echo "=== DeleteWriteBuilder ==="
javap -cp "$JAR" 'org.apache.iceberg.parquet.Parquet$DeleteWriteBuilder' | head -32
echo "=== GenericParquetWriter ==="
javap -cp "$JAR" org.apache.iceberg.data.parquet.GenericParquetWriter | head -12
echo "=== EqualityDeleteWriter ==="
javap -cp "$JAR" org.apache.iceberg.deletes.EqualityDeleteWriter | head -15
echo "=== RowDelta ==="
javap -cp "$JAR" org.apache.iceberg.RowDelta | head -20
