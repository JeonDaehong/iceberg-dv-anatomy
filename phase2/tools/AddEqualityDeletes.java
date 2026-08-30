/*
 * equality delete 파일을 테이블에 추가한다.
 *
 * 왜 Java 인가: Spark 커넥터는 equality delete 를 쓰지 못한다. DELETE 는 항상
 * position delete(V3 이면 DV)로 나간다. equality delete 는 Flink 나 Java API 로만 만든다.
 *
 * 왜 필요한가: §7.1 패치는 equality delete 가 있으면 기존 행별 루프로 폴백한다.
 * "코드를 안 건드렸다" 와 "느려지지 않았다" 는 다른 말이므로, 그 경로를 실제로 재야 한다.
 * 특히 DV 와 equality delete 가 '섞인' 테이블이 중요하다 — 게이트가 빠른 경로를
 * 적극적으로 거부하는 유일한 경우이기 때문이다.
 *
 * 삭제 술어: id % modulus == 0  인 id 들을 equality delete 로 기록한다.
 * (equality delete 는 값 일치로 지우므로, 지울 id 목록을 그대로 파일에 쓴다)
 */
import java.io.IOException;
import java.util.List;
import java.util.UUID;
import org.apache.hadoop.conf.Configuration;
import org.apache.iceberg.DeleteFile;
import org.apache.iceberg.PartitionSpec;
import org.apache.iceberg.Schema;
import org.apache.iceberg.Table;
import org.apache.iceberg.catalog.TableIdentifier;
import org.apache.iceberg.data.GenericRecord;
import org.apache.iceberg.data.Record;
import org.apache.iceberg.data.parquet.GenericParquetWriter;
import org.apache.iceberg.deletes.EqualityDeleteWriter;
import org.apache.iceberg.hadoop.HadoopCatalog;
import org.apache.iceberg.io.OutputFile;
import org.apache.iceberg.parquet.Parquet;
import org.apache.iceberg.types.Types;

public class AddEqualityDeletes {

  public static void main(String[] args) throws IOException {
    if (args.length < 4) {
      System.err.println(
          "usage: AddEqualityDeletes <warehouse> <namespace.table> <totalRows> <modulus>");
      System.exit(2);
    }
    String warehouse = args[0];
    String tableName = args[1];
    long totalRows = Long.parseLong(args[2]);
    long modulus = Long.parseLong(args[3]);

    HadoopCatalog catalog = new HadoopCatalog(new Configuration(), warehouse);
    Table table = catalog.loadTable(TableIdentifier.parse(tableName));

    Types.NestedField idField = table.schema().findField("id");
    if (idField == null) {
      throw new IllegalStateException("no 'id' column in " + tableName);
    }
    Schema deleteSchema = new Schema(idField);

    String path =
        table.location() + "/data/eq-delete-" + UUID.randomUUID() + ".parquet";
    OutputFile out = table.io().newOutputFile(path);

    EqualityDeleteWriter<Record> writer =
        Parquet.writeDeletes(out)
            .forTable(table)
            .rowSchema(deleteSchema)
            .createWriterFunc(GenericParquetWriter::create)
            .equalityFieldIds(List.of(idField.fieldId()))
            .overwrite()
            .withSpec(PartitionSpec.unpartitioned())
            .buildEqualityWriter();

    long written = 0;
    try (EqualityDeleteWriter<Record> w = writer) {
      GenericRecord record = GenericRecord.create(deleteSchema);
      for (long id = 0; id < totalRows; id += modulus) {
        w.write(record.copy("id", id));
        written++;
      }
    }

    DeleteFile deleteFile = writer.toDeleteFile();
    table.newRowDelta().addDeletes(deleteFile).commit();

    System.out.printf(
        "table=%s  equality deletes=%,d  file=%s  bytes=%,d%n",
        tableName, written, deleteFile.location(), deleteFile.fileSizeInBytes());
  }
}
